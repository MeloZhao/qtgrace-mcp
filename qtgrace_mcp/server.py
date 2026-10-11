#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qtgrace MCP Server — 通过 TCP 远程控制 qtgrace (Grace 的 Qt 移植版) 绘图。

协议来源: qtgrace 源码 src/Server.cpp + examples/qtclient/ (已验证可行)。
控制通道: qtgrace.exe -ServerMode <write_port> <read_port>
  - write_port: qtgrace 的 write server (client 收响应)
  - read_port:  qtgrace 的 read server (client 发命令)
每批操作新建 TCP 连接, 发完整数据流: [cmd][len][payload]...

命令 (getCommandFromClient):
  1=PLOT_INFO  2=WRITE_DATAVEC  4=REDRAW  5=PS_FILENAME  7=REDRAW_AND_WRITEPS
  12=WRITE_DATAVEC_FINISHED  42=KILL_CHILD  99=END_COMM
ComMode: 0=initComm 1=endComm 2=readDataComm

回退模式: 若 TCP 不可用, 自动生成 .agr 脚本并用 `-batch` 渲染 (无 GUI 实时控制)。

依赖: mcp>=2.0, Pillow>=9.0
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Optional, Sequence

logger = logging.getLogger("qtgrace_mcp")

try:
    from mcp.server.fastmcp import FastMCP
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False

# Batch-engine helpers from this same package.
#   printer_guard  — an unreachable network default printer makes qtgrace block
#                    forever on a modal dialog; the guard swaps in a local one.
#   convert_pnm_bitmap — PNG/JPEG go through Qt's raster engine and segfault on
#                    some projects; PNM uses Grace's own driver and never does.
from .printer import printer_guard  # noqa: E402
from .batch import _convert_bitmap as convert_pnm_bitmap  # noqa: E402

# ---------------- 协议常量 ----------------
PLOT_INFO = 1
WRITE_DATAVEC = 2
REDRAW = 4
PS_FILENAME = 5
SET_SCALING_MODE = 6
REDRAW_AND_WRITEPS = 7
SET_LAYOUT_MODE = 8
WRITE_DATAVEC_FINISHED = 12
KILL_CHILD = 42
END_COMM = 99

initComm, endComm, readDataComm = 0, 1, 2

# 支持的输出设备映射
DEVICE_MAP: dict[str, str] = {
    "png": "PNG", "ps": "PS", "eps": "EPS", "svg": "SVG",
    "jpg": "JPEG", "jpeg": "JPEG", "bmp": "BMP",
    "tif": "TIFF", "tiff": "TIFF", "xpm": "XPM", "pdf": "PDF",
}

# 设备 -> 输出文件扩展名 (qtgrace -hardcopy 把输出写成 <stem>.<ext> 落到 cwd)
DEV_EXT: dict[str, str] = {
    "PNG": "png", "PS": "ps", "EPS": "eps", "SVG": "svg",
    "JPEG": "jpg", "BMP": "bmp", "TIFF": "tif", "PDF": "pdf", "XPM": "xpm",
}

# 默认 qtgrace 官方示例目录 (用于 "复刻示例" 功能)
DEFAULT_EXAMPLES_DIR = r"D:\qtgrace_v026_Win7\examples"

# Set 类型 -> 数据列数 (来自 Grace UsersGuide 表 "Set types")
# TCP 协议 WRITE_DATAVEC 仅支持 2 列, 多列类型必须用 batch 模式
SET_TYPE_COLS: dict[str, int] = {
    "xy": 2,
    "xydx": 3, "xydy": 3,
    "xydxdx": 4, "xydydy": 4, "xydxdy": 4,
    "xydxdxdydy": 6,
    "bar": 2, "bardy": 3, "bardydy": 4,
    "xyz": 3, "xyr": 3, "xysize": 3, "xycolor": 3,
    "xycolpat": 4, "xyvmap": 4,
    "xyboxplot": 6,
}

# 仅需 2 列、可在 TCP 模式下直接绘制的 set_type (通过 @sN type 切换显示样式)
TWO_COL_SET_TYPES = {"xy", "bar"}

# 预设样式: 名称 -> Grace 命令文本 (可直接作为 style 参数传入)
# 用户传 style="journal" 时自动展开为以下命令; 也可传自定义命令覆盖
PRESET_STYLES: dict[str, str] = {
    # 期刊/发表风格: 无网格, Helvetica, 线宽2, 图例无边框
    "journal": (
        "@frame linewidth 2\n"
        "@xaxis ticklabel font 4\n"
        "@yaxis ticklabel font 4\n"
        "@xaxis label font 4 size 1.2\n"
        "@yaxis label font 4 size 1.2\n"
        "@title font 4 size 1.5\n"
        "@legend font 4 char size 1.0\n"
        "@legend box off\n"
        "@xaxis grid off\n"
        "@yaxis grid off\n"
        "@s0 line width 2\n"
        "@s1 line width 2\n"
        "@s2 line width 2\n"
        "@s3 line width 2\n"
    ),
    # 极简风格: 无网格, 无上/右轴刻度
    "minimal": (
        "@xaxis grid off\n"
        "@yaxis grid off\n"
        "@altxaxis off\n"
        "@altyaxis off\n"
        "@legend box off\n"
    ),
    # 深色背景风格
    "dark": (
        "@page background color 0\n"
        "@frame color 1\n"
        "@xaxis label color 1\n"
        "@yaxis label color 1\n"
        "@xaxis ticklabel color 1\n"
        "@yaxis ticklabel color 1\n"
        "@title color 1\n"
        "@legend color 1\n"
        "@s0 line color 2\n"
        "@s1 line color 3\n"
        "@s2 line color 4\n"
        "@s3 line color 5\n"
    ),
    # 演示/大字体风格
    "presentation": (
        "@title size 2.0\n"
        "@xaxis label size 1.5\n"
        "@yaxis label size 1.5\n"
        "@xaxis ticklabel size 1.2\n"
        "@yaxis ticklabel size 1.2\n"
        "@legend char size 1.2\n"
        "@frame linewidth 3\n"
        "@s0 line width 3\n"
        "@s1 line width 3\n"
        "@s2 line width 3\n"
    ),
    # 带网格风格 (数据分析用)
    "grid": (
        "@xaxis grid on\n"
        "@yaxis grid on\n"
        "@xaxis grid linestyle 2\n"
        "@yaxis grid linestyle 2\n"
        "@xaxis grid color 7\n"
        "@yaxis grid color 7\n"
    ),
}


def _resolve_style(style: Optional[str]) -> Optional[str]:
    """解析 style 参数: 若是预设名称则展开, 否则原样返回。

    支持 'journal+@s0 line color 2' 形式: 预设名 + 自定义命令叠加。
    """
    if not style:
        return None
    s = style.strip()
    # 检查是否以预设名开头 (可能后跟 + 自定义命令)
    for name, preset in PRESET_STYLES.items():
        if s == name:
            return preset
        if s.startswith(name + "+"):
            extra = s[len(name) + 1:].strip()
            return preset + ("\n" + extra if extra else "")
    return s


def _default_qtgrace_path() -> str:
    """探测 qtgrace 可执行文件路径。

    优先级: 环境变量 QTGRACE_PATH > 常见安装位置。
    """
    env = os.environ.get("QTGRACE_PATH")
    if env:
        return env
    candidates = [
        r"D:\qtgrace_v026_Win7\bin\qtgrace.exe",
        r"C:\qtgrace\bin\qtgrace.exe",
        r"C:\Program Files\qtgrace\bin\qtgrace.exe",
        "/usr/local/bin/qtgrace",
        "/usr/bin/qtgrace",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


DEFAULT_QTGRACE = _default_qtgrace_path()


class QtGraceError(RuntimeError):
    """qtgrace 操作异常。"""
    pass


class QtGraceController:
    """qtgrace 进程 + TCP 控制封装。

    线程安全: 每个公共方法内部使用 _lock 串行化 TCP 会话。
    """

    def __init__(self, qtgrace_path: str = DEFAULT_QTGRACE,
                 workdir: Optional[str] = None):
        self.path = os.path.abspath(qtgrace_path)
        if not os.path.exists(self.path):
            raise QtGraceError(f"qtgrace 可执行文件不存在: {self.path}")
        self.workdir = workdir or os.path.join(tempfile.gettempdir(), "qtgrace_mcp")
        os.makedirs(self.workdir, exist_ok=True)
        self.proc: Optional[subprocess.Popen] = None
        self.write_port: Optional[int] = None
        self.read_port: Optional[int] = None
        self._sock_w: Optional[socket.socket] = None
        self._sock_r: Optional[socket.socket] = None
        self.export_name: Optional[str] = None
        self.device: str = "PNG"  # 当前输出设备
        self._lock = threading.Lock()

    # ---------- 进程管理 ----------
    def start(self, device: str = "PNG") -> str:
        """启动 qtgrace。device: PNG/EPS/PS/SVG/JPEG/BMP 等输出设备。"""
        with self._lock:
            return self._start_locked(device)

    def _start_locked(self, device: str = "PNG") -> str:
        if self.proc and self.proc.poll() is None:
            return (f"qtgrace 已在运行 (write={self.write_port}, "
                    f"read={self.read_port}, device={self.device})")
        device = (device or "PNG").upper().lstrip(".")
        # 随机选空闲端口
        with socket.socket() as s1, socket.socket() as s2:
            s1.bind(("127.0.0.1", 0))
            s2.bind(("127.0.0.1", 0))
            self.write_port = s1.getsockname()[1]
            self.read_port = s2.getsockname()[1]
        env = dict(os.environ)
        # 避免首次运行的设置向导阻塞
        env.setdefault("QTGRACE_NO_WIZARD", "1")
        args = [self.path, "-ServerMode", str(self.write_port), str(self.read_port)]
        if device != "PNG":
            args += ["-hdevice", device]
        self.device = device
        logger.info("启动 qtgrace: %s (write=%d, read=%d, device=%s)",
                    self.path, self.write_port, self.read_port, device)
        self.proc = subprocess.Popen(
            args,
            cwd=self.workdir, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # 等待端口就绪
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise QtGraceError(f"qtgrace 启动即退出, code={self.proc.returncode}")
            try:
                with socket.create_connection(("127.0.0.1", self.write_port), timeout=0.5):
                    with socket.create_connection(("127.0.0.1", self.read_port), timeout=0.5):
                        break
            except OSError:
                time.sleep(0.3)
        else:
            raise QtGraceError("qtgrace TCP 端口 10 秒内未就绪")
        return (f"qtgrace 已启动 (pid={self.proc.pid}, write={self.write_port}, "
                f"read={self.read_port}, device={self.device}, workdir={self.workdir})")

    def stop(self) -> str:
        with self._lock:
            return self._stop_locked()

    def _stop_locked(self) -> str:
        if not self.proc or self.proc.poll() is not None:
            self._cleanup_sockets()
            return "qtgrace 未在运行"
        try:
            self._session_locked(lambda sr, sw: self._send_stream(sr, sw, struct.pack("i", KILL_CHILD)))
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._cleanup_sockets()
        logger.info("qtgrace 已关闭")
        return "qtgrace 已关闭"

    def is_running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def _cleanup_sockets(self) -> None:
        for s in (self._sock_w, self._sock_r):
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        self._sock_w = self._sock_r = None

    # ---------- TCP 会话 ----------
    def _connect(self) -> tuple[socket.socket, socket.socket]:
        if not self.is_running():
            raise QtGraceError("qtgrace 未运行, 请先调用 start_qtgrace")
        sr = socket.create_connection(("127.0.0.1", self.read_port), timeout=5)
        sw = socket.create_connection(("127.0.0.1", self.write_port), timeout=5)
        sr.settimeout(15)
        sw.settimeout(15)
        sr.sendall(struct.pack("i", initComm))
        resp = struct.unpack("i", sw.recv(4))[0]
        if resp != initComm:
            sr.close()
            sw.close()
            raise QtGraceError(f"握手失败: 期望 {initComm}, 得到 {resp}")
        return sr, sw

    @staticmethod
    def _send_stream(sr: socket.socket, sw: socket.socket, payload: bytes) -> int:
        sr.sendall(struct.pack("i", readDataComm))
        sr.sendall(struct.pack("i", len(payload)))
        sr.sendall(payload)
        resp = struct.unpack("i", sw.recv(4))[0]
        return resp

    def _session(self, fn):
        """新会话: 连接->执行->关闭 (外部已持锁时用 _session_locked)。"""
        with self._lock:
            return self._session_locked(fn)

    def _session_locked(self, fn):
        """新会话: 连接->执行->关闭 (调用方必须已持 _lock)。"""
        sr, sw = self._connect()
        try:
            return fn(sr, sw)
        finally:
            try:
                sr.close()
            except Exception:
                pass
            try:
                sw.close()
            except Exception:
                pass

    # ---------- 数据流构造 ----------
    @staticmethod
    def _cmd(c: int) -> bytes:
        return struct.pack("i", c)

    @staticmethod
    def _plot_info(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack("i", PLOT_INFO) + struct.pack("i", len(b)) + b

    @staticmethod
    def _datavec(xs: Sequence[float], ys: Sequence[float]) -> bytes:
        n = len(xs)
        if len(ys) != n:
            raise QtGraceError("x/y 长度不一致")
        out = struct.pack("i", WRITE_DATAVEC) + struct.pack("i", n)
        out += struct.pack("d" * n, *xs) + struct.pack("d" * n, *ys)
        return out

    @staticmethod
    def _ps_filename(name: str) -> bytes:
        b = name.encode("utf-8")
        return struct.pack("i", PS_FILENAME) + struct.pack("i", len(b)) + b

    # ---------- 高层操作 ----------
    def plot(self, datasets: list,
             title: str = "", xlabel: str = "", ylabel: str = "",
             legends: Optional[list[str]] = None,
             style: Optional[str] = None,
             set_type: str = "xy",
             xmin: Optional[float] = None, xmax: Optional[float] = None,
             ymin: Optional[float] = None, ymax: Optional[float] = None) -> str:
        """发送数据并成图。

        datasets: [[col1_list, col2_list, ...], ...] — 每个数据集是列的列表。
                  xy/bar 类型为 [[x, y], ...] (2列);
                  xydy 为 [[x, y, dy], ...] (3列) 等。
        set_type: xy / xydy / xydxdy / bar / bardy / xyboxplot 等 (见 SET_TYPE_COLS)。
                  TCP 模式仅支持 2 列类型 (xy/bar), 多列类型请用 batch 模式。
        xmin/xmax/ymin/ymax: 可选, 显式设置世界坐标范围 (@world 命令)。
        """
        if not datasets:
            raise QtGraceError("没有数据")
        set_type = (set_type or "xy").lower()
        expected_cols = SET_TYPE_COLS.get(set_type)
        if expected_cols is None:
            raise QtGraceError(
                f"未知 set_type: {set_type}. 支持: {', '.join(sorted(SET_TYPE_COLS))}")
        if expected_cols > 2:
            raise QtGraceError(
                f"set_type={set_type} 需要 {expected_cols} 列数据, "
                f"TCP 协议仅支持 2 列 (xy/bar)。"
                f"请使用 plot_and_export(..., use_batch=True) 走 batch 渲染。")
        for ds in datasets:
            if len(ds) != expected_cols:
                raise QtGraceError(
                    f"set_type={set_type} 每个数据集需 {expected_cols} 列, 得到 {len(ds)} 列")
        parts = [self._plot_info("@FLUSH\n"), self._plot_info("@ g0 on\n")]
        if title:
            parts.append(self._plot_info(f'@title "{title}"\n'))
        if xlabel:
            parts.append(self._plot_info(f'@xaxis label "{xlabel}"\n'))
        if ylabel:
            parts.append(self._plot_info(f'@yaxis label "{ylabel}"\n'))
        if xmin is not None:
            parts.append(self._plot_info(f"@world xmin {xmin}\n"))
        if xmax is not None:
            parts.append(self._plot_info(f"@world xmax {xmax}\n"))
        if ymin is not None:
            parts.append(self._plot_info(f"@world ymin {ymin}\n"))
        if ymax is not None:
            parts.append(self._plot_info(f"@world ymax {ymax}\n"))
        if legends:
            for i, lg in enumerate(legends):
                parts.append(self._plot_info(f'@s{i} legend "{lg}"\n'))
                parts.append(self._plot_info(f"@s{i} legend on\n"))
        # 设置每个数据集的 type (bar 等2列类型可通过此命令切换显示样式)
        if set_type != "xy":
            for i in range(len(datasets)):
                parts.append(self._plot_info(f"@s{i} type {set_type}\n"))
        resolved_style = _resolve_style(style)
        if resolved_style:
            parts.append(self._plot_info(
                resolved_style if resolved_style.endswith("\n") else resolved_style + "\n"))
        for ds in datasets:
            xs, ys = ds[0], ds[1]
            parts.append(self._datavec(xs, ys))
            parts.append(self._cmd(WRITE_DATAVEC_FINISHED))
        parts.append(self._cmd(REDRAW))
        parts.append(self._cmd(END_COMM))
        stream = b"".join(parts)

        def run(sr, sw):
            resp = self._send_stream(sr, sw, stream)
            if resp != endComm:
                raise QtGraceError(f"绘图失败: 响应 {resp}")
            return resp

        with self._lock:
            self._session_locked(run)
        n = len(datasets)
        total_pts = sum(len(ds[0]) for ds in datasets)
        logger.debug("已绘制 %d 个数据集 (type=%s), 共 %d 个点", n, set_type, total_pts)
        return f"已绘制 {n} 个数据集 (type={set_type}), 共 {total_pts} 个点"

    def save_agr(self, filename: str) -> str:
        """通过 TCP 让 qtgrace 生成当前项目的 .agr 参数文本并保存。

        流程: 发 sendDataCom(3) -> qtgrace 回 [sendDataCom][doclen][docname][len][.agr文本]
        filename: 输出 .agr 路径(绝对路径或相对 workdir)。
        """
        if not self.is_running():
            raise QtGraceError("qtgrace 未运行")

        # 绝对路径 -> % 前缀
        if os.path.isabs(filename):
            save_name = "%" + filename
        else:
            save_name = filename
        fname_bytes = save_name.encode("utf-8")
        payload = struct.pack("i", PS_FILENAME) + struct.pack("i", len(fname_bytes)) + fname_bytes

        def run(sr, sw):
            # 1) 设置 docname (让 qtgrace 记住名字)
            sr.sendall(struct.pack("i", readDataComm))
            sr.sendall(struct.pack("i", len(payload)))
            sr.sendall(payload)
            r1 = struct.unpack("i", sw.recv(4))[0]
            if r1 != endComm:
                raise QtGraceError(f"设置 docname 失败: {r1}")
            # 2) 请求参数 (sendDataCom=3)
            sr.sendall(struct.pack("i", 3))  # sendDataCom
            # qtgrace 响应: [sendDataCom][bytesToSend][data][endComm]
            # data = [doclen][docname][paramLen][.agr文本]
            def recv_exact(n: int) -> bytes:
                buf = b""
                while len(buf) < n:
                    chunk = sw.recv(n - len(buf))
                    if not chunk:
                        raise QtGraceError("连接中断, 接收数据不完整")
                    buf += chunk
                return buf

            mode = struct.unpack("i", recv_exact(4))[0]
            if mode != 3:
                raise QtGraceError(f"预期 sendDataCom(3), 得到 {mode}")
            total = struct.unpack("i", recv_exact(4))[0]
            data = recv_exact(total)
            # 解析 data: [doclen][docname][paramLen][pp]
            off = 0
            dl = struct.unpack("i", data[off:off + 4])[0]
            off += 4
            off += dl  # docname
            pl = struct.unpack("i", data[off:off + 4])[0]
            off += 4
            agr_text = data[off:off + pl].decode("utf-8", errors="replace")
            return agr_text

        with self._lock:
            agr_text = self._session_locked(run)

        # 保存
        if os.path.isabs(filename):
            out = filename
        else:
            out = os.path.join(self.workdir, filename)
        if os.path.dirname(out):
            os.makedirs(os.path.dirname(out), exist_ok=True)
        # 显式设置上/右轴(altxaxis/altyaxis)关闭: tick size 0 + 全 off
        alt_axis_block = (
            "@    altxaxis  off\n"
            "@    altxaxis  tick off\n"
            "@    altxaxis  tick major size 0\n"
            "@    altxaxis  tick minor size 0\n"
            "@    altxaxis  ticklabel off\n"
            "@    altxaxis  bar off\n"
            "@    altyaxis  off\n"
            "@    altyaxis  tick off\n"
            "@    altyaxis  tick major size 0\n"
            "@    altyaxis  tick minor size 0\n"
            "@    altyaxis  ticklabel off\n"
            "@    altyaxis  bar off\n"
        )
        # 移除已有的单行 altxaxis/altyaxis off/on 行, 替换为完整块
        # 修复: 原正则 [x]? 是字符类写法有误, 应直接用 altxaxis / altyaxis
        agr_text = re.sub(r"@\s+altxaxis\s+(on|off)\s*\n", "", agr_text)
        agr_text = re.sub(r"@\s+altyaxis\s+(on|off)\s*\n", "", agr_text)
        # 插入到 @with g0 之后 (altxaxis 是 g0 的属性, 必须在 @with g0 作用域内)
        marker = "@with g0"
        if marker in agr_text:
            idx = agr_text.find(marker)
            nl = agr_text.find("\n", idx)
            insert_at = nl + 1 if nl != -1 else len(agr_text)
            agr_text = agr_text[:insert_at] + alt_axis_block + agr_text[insert_at:]
        else:
            agr_text = agr_text.rstrip() + "\n\n" + alt_axis_block
        with open(out, "w", encoding="utf-8") as f:
            f.write(agr_text)
        logger.info("已保存 .agr: %s", out)
        return out

    def set_scaling_mode(self, mode: int = 1, graph_no: int = 0,
                         x_start: float = 0.0, x_end: float = 0.0) -> str:
        """设置缩放模式 (命令 6, 对应官方 sendScalingModeInfo)。

        mode: 0=DEFAULT_LAYOUT(不动), 1=AUTOSCALE_ALL_AXES(默认),
              2=AUTOSCALE_Y_AXIS_OR_OVERLAY(只缩放 y, 用 x_start/x_end)
        """
        payload = struct.pack("i", SET_SCALING_MODE)
        payload += struct.pack("i", graph_no)
        payload += struct.pack("i", mode)
        if mode == 2:
            payload += struct.pack("d", x_start) + struct.pack("d", x_end)
        with self._lock:
            resp = self._session_locked(lambda sr, sw: self._send_stream(sr, sw, payload))
        if resp != endComm:
            raise QtGraceError(f"SET_SCALING_MODE 失败: {resp}")
        return f"scaling mode={mode} graph={graph_no}"

    def set_layout_mode(self, layout: int = 0, graph_no: int = 0,
                        cols: int = 1, num_graphs: int = 1) -> str:
        """设置图布局 (命令 8, 对应官方 sendLayoutModeInfo)。

        layout: 0=DEFAULT, 1=AUTOSCALE_ALL_AXES_OR_JOIN_PLOT(join),
                2=AUTOSCALE_Y_AXIS_OR_OVERLAY(overlay), 3=GRAPH_POSITION(排列)
        """
        payload = struct.pack("i", SET_LAYOUT_MODE)
        payload += struct.pack("i", graph_no)
        payload += struct.pack("i", layout)
        if layout == 3:
            payload += struct.pack("i", cols) + struct.pack("i", num_graphs)
        with self._lock:
            resp = self._session_locked(lambda sr, sw: self._send_stream(sr, sw, payload))
        if resp != endComm:
            raise QtGraceError(f"SET_LAYOUT_MODE 失败: {resp}")
        return f"layout={layout} graph={graph_no}"

    def plot_and_export(self, datasets, filename="plot", title="", xlabel="", ylabel="",
                        legends=None, style=None, format="png",
                        set_type="xy",
                        xmin=None, xmax=None, ymin=None, ymax=None) -> str:
        """plot() + export() 一步完成。"""
        self.plot(datasets, title, xlabel, ylabel, legends, style, set_type,
                  xmin, xmax, ymin, ymax)
        return self.export(filename, format)

    def export(self, filename: str, format: str = "png") -> str:
        """设置导出名并导出图像。返回输出文件绝对路径。

        格式由启动时的 -hdevice 决定; 若 format 与当前设备不符,
        自动重启 qtgrace 到对应设备 (需重新 plot)。
        注意: 文件名须为相对路径(写入 workdir)或 %绝对路径。
        """
        fmt = (format or "png").lower().lstrip(".")
        target_dev = DEVICE_MAP.get(fmt, "PNG")
        if self.device != target_dev:
            raise QtGraceError(
                f"当前设备 {self.device} 不支持导出 {fmt}。"
                f"请用 start_qtgrace(device=\"{target_dev}\") 重启 qtgrace 后重新绘图导出。"
                f"支持格式: {', '.join(sorted(DEVICE_MAP))}")
        base = filename if filename else "plot"
        # 无扩展名则补默认
        if "." not in os.path.basename(base):
            base = f"{base}.{fmt}"
        # 绝对路径 -> 加 % 前缀 (qtgrace 约定)
        psname = base
        if os.path.isabs(base):
            psname = "%" + base

        def run(sr, sw):
            r1 = self._send_stream(sr, sw, self._ps_filename(psname))
            if r1 != endComm:
                raise QtGraceError(f"设置导出名失败: {r1}")
            r2 = self._send_stream(sr, sw, self._cmd(REDRAW_AND_WRITEPS))
            if r2 != endComm:
                raise QtGraceError(f"导出失败: {r2}")
            return r2

        with self._lock:
            self._session_locked(run)

        time.sleep(1.0)
        # 计算实际输出路径
        if os.path.isabs(base):
            out = base
        else:
            out = os.path.join(self.workdir, base)
        # 等待文件出现 (qtgrace 异步写)
        deadline = time.time() + 10
        while time.time() < deadline:
            if os.path.exists(out) and os.path.getsize(out) > 0:
                break
            time.sleep(0.3)
        if not os.path.exists(out):
            raise QtGraceError(f"导出文件未生成: {out}")
        # 后处理: 清除 qtgrace 渲染 bug 产生的绘图框外顶部残留刻度线 (仅 PNG)
        if fmt == "png":
            try:
                self._clean_top_ticks(out)
            except Exception as e:
                logger.warning("PNG 后处理失败 (不影响导出): %s", e)
        logger.info("已导出图像: %s (%d bytes)", out, os.path.getsize(out))
        return out

    @staticmethod
    def _clean_top_ticks(png_path: str) -> None:
        """清除绘图框上边界之上 12px 内的深色像素 (qtgrace 多曲线渲染 bug 的残留刻度线)。

        保留绘图框上边线 (top 本身), 只涂白 top-12..top-1 区域。
        数据曲线和标题都在框内或下方, 不受影响。
        使用 numpy 向量化加速 (若可用), 否则回退到逐像素。
        """
        try:
            from PIL import Image
            import numpy as np
        except ImportError:
            try:
                from PIL import Image
            except ImportError:
                return
            np = None

        im = Image.open(png_path).convert("RGB")
        w, h = im.size

        if np is not None:
            # 向量化: 用 numpy 统计深色像素
            arr = np.array(im)
            # 深色: r+g+b < 450
            dark = arr.sum(axis=2) < 450
            col_dark = dark.sum(axis=0)
            row_dark = dark.sum(axis=1)
            cand = np.where(col_dark > 100)[0]
            if len(cand) < 2:
                return
            left, right = int(cand[0]), int(cand[-1])
            cand2 = np.where(row_dark > 100)[0]
            if len(cand2) < 2:
                return
            top = int(cand2[0])
            y0 = max(0, top - 12)
            x0 = max(0, left - 15)
            x1 = min(w, right + 15)
            # 涂白
            arr[y0:top, x0:x1] = 255
            Image.fromarray(arr).save(png_path)
        else:
            # 回退: 逐像素 (慢, 仅用于无 numpy 环境)
            px = im.load()
            col_dark = [0] * w
            row_dark = [0] * h
            for y in range(h):
                for x in range(w):
                    r, g, b = px[x, y][:3]
                    if r + g + b < 450:
                        col_dark[x] += 1
                        row_dark[y] += 1
            cand = [x for x in range(w) if col_dark[x] > 100]
            if len(cand) < 2:
                return
            left, right = cand[0], cand[-1]
            cand2 = [y for y in range(h) if row_dark[y] > 100]
            if len(cand2) < 2:
                return
            top = cand2[0]
            y0 = max(0, top - 12)
            x0 = max(0, left - 15)
            x1 = min(w, right + 15)
            for y in range(y0, top):
                for x in range(x0, x1):
                    px[x, y] = (255, 255, 255)
            im.save(png_path)


def build_batch_agr(datasets, title="", xlabel="", ylabel="", legends=None,
                    set_type="xy", style=None,
                    xmin=None, xmax=None, ymin=None, ymax=None,
                    data_files: Optional[list[str]] = None) -> str:
    """生成 qtgrace batch 命令脚本 (用于 -batch 模式)。

    注意: qtgrace -batch 模式执行的是命令脚本 (不带 @ 前缀), 不是 .agr 项目文件。
    datasets: [[col1_list, col2_list, ...], ...] — 每个数据集是列的列表。
    set_type: xy/xydy/xydxdy/bar/bardy/xyboxplot 等 (见 SET_TYPE_COLS)。
    data_files: 可选, 外部数据文件路径列表 (与 datasets 一一对应)。
    """
    set_type = (set_type or "xy").lower()
    expected_cols = SET_TYPE_COLS.get(set_type, 2)
    # 验证数据
    for ds in datasets:
        if len(ds) != expected_cols:
            raise QtGraceError(
                f"set_type={set_type} 需要 {expected_cols} 列数据, 得到 {len(ds)} 列")
        n = len(ds[0])
        for col in ds:
            if len(col) != n:
                raise QtGraceError("同一数据集的各列长度不一致")

    lines = []
    # 读取每个数据集 (依次进入 S0, S1, S2...)
    for i, ds in enumerate(datasets):
        if data_files and i < len(data_files) and data_files[i]:
            lines.append(f'READ {set_type.upper()} "{data_files[i]}"')
        else:
            # 内联数据: 临时文件由调用方生成, 这里不处理
            raise QtGraceError("batch 模式需要 data_files (外部数据文件)")
    if title:
        lines.append(f'TITLE "{title}"')
    if xlabel:
        lines.append(f'XAXIS LABEL "{xlabel}"')
    if ylabel:
        lines.append(f'YAXIS LABEL "{ylabel}"')
    # 坐标范围
    if xmin is not None:
        lines.append(f"WORLD XMIN {xmin}")
    if xmax is not None:
        lines.append(f"WORLD XMAX {xmax}")
    if ymin is not None:
        lines.append(f"WORLD YMIN {ymin}")
    if ymax is not None:
        lines.append(f"WORLD YMAX {ymax}")
    # 每个数据集的外观
    for i in range(len(datasets)):
        legend = legends[i] if legends and i < len(legends) else ""
        if legend:
            lines.append(f'S{i} LEGEND "{legend}"')
        if set_type in ("bar", "bardy", "bardydy"):
            lines.append(f"S{i} LINE TYPE 0")
            lines.append(f"S{i} SYMBOL 0")
            lines.append(f"S{i} FILL TYPE 1")
        elif set_type == "xyboxplot":
            lines.append(f"S{i} LINE TYPE 1")
            lines.append(f"S{i} SYMBOL 0")
        else:
            lines.append(f"S{i} LINE TYPE 1")
            lines.append(f"S{i} SYMBOL 1")
            lines.append(f"S{i} SYMBOL SIZE 0.5")
    # 预设样式 (去掉 @ 前缀, 转为命令脚本格式)
    resolved_style = _resolve_style(style)
    if resolved_style:
        for line in resolved_style.split("\n"):
            line = line.strip()
            if line.startswith("@"):
                line = line[1:].strip()
            if line:
                lines.append(line)
    # 自动缩放 (如果没有显式设置全部坐标范围)
    if not all(v is not None for v in (xmin, xmax, ymin, ymax)):
        lines.append("AUTOSCALE")
    return "\n".join(lines) + "\n"


def _hardcopy_render(qtgrace_path: str, agr_path: str, out_path: str,
                     fmt: str = "png", cwd: Optional[str] = None,
                     timeout: int = 90) -> str:
    """用 qtgrace -hardcopy 渲染一个 .agr 为图像 (项目文件或命令脚本均可)。

    关键行为 (qtgrace v026 实测):
      - 输出文件名被忽略: 无论 -printfile 传什么, qtgrace 都把输出写成
        <输入文件名(去扩展名)>.<设备扩展名>, 并落到【进程 cwd】。
      - 因此这里用 cwd 控制落点, 渲染完成后把该文件 rename 到 out_path。
      - cwd 默认取输入 .agr 所在目录, 以保证其中相对路径的 READ 数据可被解析。
      - 成功判据 = 输出文件存在且非空 (segfault 时可能 rc=0 但不产生文件)。
    """
    fmt = (fmt or "png").lstrip(".")
    dev = DEVICE_MAP.get(fmt)
    if not dev:
        raise QtGraceError(
            f"不支持的格式: {fmt}。支持: {', '.join(sorted(DEVICE_MAP))}")
    ext = DEV_EXT.get(dev, fmt)
    agr_path = os.path.abspath(agr_path)
    if not os.path.exists(agr_path):
        raise QtGraceError(f"输入文件不存在: {agr_path}")
    run_cwd = cwd or os.path.dirname(agr_path) or "."
    os.makedirs(run_cwd, exist_ok=True)
    stem = os.path.splitext(os.path.basename(agr_path))[0]

    def _run_once(device: str, suffix: str) -> Optional[str]:
        """One qtgrace run.  Returns the produced path, or None on failure."""
        target = os.path.join(run_cwd, f"{stem}.{suffix}")
        # 清理可能残留的同名文件, 避免误判
        if os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass
        # 打印机保护: 默认打印机若是不可达的网络打印机, qtgrace 会弹模态框
        # 「正在等待打印机连接…」并永久阻塞。guard 在渲染期间临时切到本地设备。
        with printer_guard():
            proc = subprocess.Popen(
                [qtgrace_path, "-hardcopy", "-hdevice", device,
                 "-printfile", "x", agr_path, "-nosigcatch", "-noask"],
                cwd=run_cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                raise QtGraceError(
                    f"渲染超时(>{timeout}s): {os.path.basename(agr_path)}\n"
                    f"最常见的原因是默认打印机不可达 —— 用 qtgrace_capabilities "
                    f"查看 printer_risky, 或先跑 qtgrace_plot.py --printers")
        # 不以 returncode 判成功: segfault 可能 rc=0 但不出图
        if os.path.exists(target) and os.path.getsize(target) > 0:
            return target
        return None

    produced = _run_once(dev, ext)

    # PNG/JPEG 是唯一走 Qt 自带光栅引擎的设备, 某些工程会让 qtgrace 段错误。
    # PNM 走 Grace 自己的驱动, 从不崩 —— 改用它渲染再转换。
    if produced is None and dev in ("PNG", "JPEG"):
        pnm = _run_once("PNM", "pnm")
        if pnm:
            out_path = os.path.abspath(out_path)
            if os.path.dirname(out_path):
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
            ok, err = convert_pnm_bitmap(pnm, out_path, dev)
            if ok:
                return out_path
            raise QtGraceError(
                f"{dev} 渲染崩溃, 且 PNM 转换失败: {err}")

    if produced is None:
        raise QtGraceError(
            f"渲染未产生有效输出(可能崩溃或数据缺失): {os.path.basename(agr_path)}")
    out_path = os.path.abspath(out_path)
    if os.path.dirname(out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if os.path.abspath(produced) != out_path:
        shutil.move(produced, out_path)
    return out_path


class QtGraceBatchRenderer:
    """无 TCP 的离线渲染器: 直接调用 qtgrace -hardcopy (每次新进程)。

    适用于已有 .agr 项目文件 (render_agr) 或由数据现场生成脚本 (render)。
    不再使用 -batch + -printfile 的旧写法 (qtgrace v026 会忽略 -printfile 文件名)。
    """

    def __init__(self, qtgrace_path: str = DEFAULT_QTGRACE,
                 workdir: Optional[str] = None):
        self.path = os.path.abspath(qtgrace_path)
        self.workdir = workdir or os.path.join(
            tempfile.gettempdir(), "qtgrace_mcp_batch")
        os.makedirs(self.workdir, exist_ok=True)

    def render_agr(self, agr_path: str, out_base: str, fmt: str = "png",
                   timeout: int = 90) -> str:
        """渲染已有 .agr (项目文件) 为图像。

        agr_path: 输入 .agr 路径 (绝对或相对); out_base: 输出基础路径(无扩展名)。
        返回输出文件绝对路径。cwd 取 agr 所在目录以保证相对数据解析。
        """
        fmt = (fmt or "png").lstrip(".")
        ext = DEV_EXT.get(DEVICE_MAP.get(fmt, fmt), fmt)
        out = f"{out_base}.{ext}"
        return _hardcopy_render(
            self.path, agr_path, out, fmt,
            cwd=os.path.dirname(os.path.abspath(agr_path)), timeout=timeout)

    def render(self, datasets, filename, title="", xlabel="", ylabel="",
               legends=None, fmt="png", set_type="xy", style=None,
               xmin=None, xmax=None, ymin=None, ymax=None,
               timeout: int = 90) -> str:
        """由数据现场生成 .agr 工程文件并 hardcopy 渲染 (无 TCP)。

        走 agr.build_agr 生成**真正的 .agr 项目文件**(数据内嵌), 而不是旧版的
        Grace 命令脚本 —— 后者被当作位置参数传给 qtgrace 时不会被当工程加载,
        结果静默不出图 (旧 test_batch_renderer 失败的原因)。
        """
        from .agr import build_agr

        tag = uuid.uuid4().hex[:8]
        agr_path = os.path.join(self.workdir, f"plot_{tag}.agr")
        fmt = (fmt or "png").lstrip(".")
        ext = DEV_EXT.get(DEVICE_MAP.get(fmt, fmt), fmt)
        out = os.path.join(self.workdir,
                           f"{os.path.basename(filename or 'plot')}.{ext}")

        # datasets 是列式: [(xs, ys), ...] 或 [(xs, ys, errs), ...]
        sets = []
        for i, ds in enumerate(datasets):
            cols = [list(c) for c in ds]
            rows = [[cols[c][r] for c in range(len(cols))]
                    for r in range(len(cols[0]))]
            entry = {"type": set_type or "xy", "data": rows}
            if legends and i < len(legends):
                entry["legend"] = legends[i]
            if style:
                entry.update({k: v for k, v in style.items()
                              if k not in ("legend", "data", "type")})
            sets.append(entry)

        graph = {"title": title, "sets": sets,
                 "xaxis": {"label": xlabel}, "yaxis": {"label": ylabel}}
        if None not in (xmin, ymin, xmax, ymax):
            graph["world"] = [xmin, ymin, xmax, ymax]

        with open(agr_path, "w", encoding="utf-8") as f:
            f.write(build_agr({"graphs": [graph]}))
        try:
            result = _hardcopy_render(self.path, agr_path, out, fmt,
                                      cwd=self.workdir, timeout=timeout)
        finally:
            try:
                os.remove(agr_path)
            except OSError:
                pass
        return result


# =====================================================================
# 示例复刻 (render the official qtgrace examples)
# =====================================================================
EXAMPLE_CATEGORIES = {
    ".agr": "Grace 项目文件 (可直接渲染)",
    ".par": "参数/样式模板",
    ".com": "Grace 批处理命令脚本",
    ".fit": "非线性拟合模板",
    ".pro": "项目/脚本",
    ".cc": "C++ 示例源码",
    ".cpp": "C++ 示例源码",
    ".h": "头文件示例",
}

# 已知在 qtgrace v026 的 -hardcopy 下会 segfault 的示例 (无法救回)
KNOWN_HARDCOPY_CRASH = {
    "charts", "logit", "pie", "terr", "tstack", "txyr", "xyz",
}


def list_examples_dir(examples_dir: str = DEFAULT_EXAMPLES_DIR) -> dict:
    """列出示例目录中的文件, 按类别分组。"""
    ex = examples_dir or DEFAULT_EXAMPLES_DIR
    result: dict = {"dir": ex, "exists": os.path.isdir(ex), "categories": {}}
    if not result["exists"]:
        return result
    cats: dict = {k: [] for k in EXAMPLE_CATEGORIES}
    for fn in sorted(os.listdir(ex)):
        ext = os.path.splitext(fn)[1].lower()
        if ext in cats:
            cats[ext].append(fn)
    result["categories"] = {k: v for k, v in cats.items() if v}
    result["agr_count"] = len(cats.get(".agr", []))
    return result


def _agr_missing_data(agr_path: str) -> Optional[list]:
    """扫描 .agr 中引用的外部数据文件, 返回不存在的文件列表 (None 表示无外部引用)。

    qtgrace 在 -hardcopy 下遇到缺失的 READ 数据文件会长时间挂起,
    因此渲染前先快筛, 避免无谓超时等待。
    """
    agr_dir = os.path.dirname(os.path.abspath(agr_path))
    try:
        with open(agr_path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return None
    refs = set(re.findall(r'"([^"]+\.(?:dat|txt|csv))"', text))
    missing = []
    for r in sorted(refs):
        if os.path.isabs(r):
            if not os.path.exists(r):
                missing.append(r)
        elif not os.path.exists(os.path.join(agr_dir, r)):
            missing.append(r)
    return missing or None


def render_example_file(examples_dir: str, name: str, fmt: str = "png",
                        outdir: Optional[str] = None,
                        timeout: int = 90) -> dict:
    """复刻单个示例 .agr。返回 {name, status, output, error, size}。"""
    ex = examples_dir or DEFAULT_EXAMPLES_DIR
    stem = name
    if stem.lower().endswith(".agr"):
        stem = stem[:-4]
    agr = os.path.join(ex, stem + ".agr")
    outdir = outdir or os.path.join(ex, "rendered")
    os.makedirs(outdir, exist_ok=True)
    ext = DEV_EXT.get(DEVICE_MAP.get((fmt or "png").lower().lstrip("."), "PNG"), "png")
    out_base = os.path.join(outdir, stem)
    rec = {"name": stem + ".agr", "status": "ok", "output": None,
           "error": None, "size": 0}
    if not os.path.exists(agr):
        rec["status"] = "missing"
        rec["error"] = f"示例不存在: {agr}"
        return rec
    # 预检: 缺失外部数据会导致 qtgrace 挂起, 提前判失败
    missing = _agr_missing_data(agr)
    if missing:
        rec["status"] = "failed"
        rec["error"] = (f"数据文件缺失: {', '.join(missing)} "
                        f"(qtgrace 缺数据时 hardcopy 会挂起, 已跳过)")
        return rec
    try:
        out = _hardcopy_render(DEFAULT_QTGRACE, agr, f"{out_base}.{ext}", fmt,
                               cwd=ex, timeout=timeout)
        rec["output"] = out
        rec["size"] = os.path.getsize(out)
    except QtGraceError as e:
        rec["status"] = "failed"
        rec["error"] = str(e)
    return rec


def render_examples_dir(examples_dir: str = DEFAULT_EXAMPLES_DIR,
                        fmt: str = "png", outdir: Optional[str] = None,
                        build_gallery: bool = True,
                        timeout: int = 90) -> dict:
    """批量复刻示例目录中所有 .agr。返回渲染报告, 可选生成 HTML 画廊。

    注意: qtgrace v026 的 hardcopy 对部分图类型 (charts/logit/pie/terr/
    tstack/txyr/xyz) 会 segfault, 这些会被标记为 failed 而非阻塞整体。
    """
    ex = examples_dir or DEFAULT_EXAMPLES_DIR
    outdir = outdir or os.path.join(ex, "gallery")
    os.makedirs(outdir, exist_ok=True)
    agrs = sorted(f for f in os.listdir(ex) if f.lower().endswith(".agr"))
    rendered, failed, missing = [], [], []
    for fn in agrs:
        rec = render_example_file(ex, fn, fmt, outdir, timeout)
        if rec["status"] == "ok":
            rendered.append(rec)
        elif rec["status"] == "failed":
            failed.append(rec)
        else:
            missing.append(rec)
    report = {
        "examples_dir": ex,
        "format": fmt,
        "outdir": outdir,
        "total": len(agrs),
        "rendered": rendered,
        "failed": failed,
        "missing": missing,
        "gallery": None,
    }
    if build_gallery:
        report["gallery"] = build_gallery_html(
            rendered, failed, outdir, ex, fmt)
    return report


GALLERY_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>qtgrace 示例复刻画廊</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; background:#0f1115; color:#e6e6e6;
         font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
  header { padding:24px 28px 12px; border-bottom:1px solid #23262d; }
  h1 { margin:0 0 4px; font-size:22px; }
  .meta { color:#8b929e; font-size:13px; }
  main { padding:20px 28px 48px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr));
          gap:18px; }
  figure { margin:0; background:#171a21; border:1px solid #23262d;
           border-radius:10px; overflow:hidden; transition:.15s; }
  figure:hover { border-color:#0D9488; transform:translateY(-2px); }
  figure img { width:100%%; height:200px; object-fit:contain;
               background:#fff; display:block; }
  figcaption { padding:8px 10px; font-size:13px; color:#cfd3da; }
  figcaption .sz { color:#6f7682; float:right; }
  .empty { color:#8b929e; }
  section { margin-top:34px; }
  h2 { font-size:16px; border-left:3px solid #0D9488; padding-left:10px; }
  ul.fails { color:#c9a1a1; font-size:13px; line-height:1.7;
             background:#1a1414; border:1px solid #2c2020; border-radius:8px;
             padding:12px 18px; list-style:none; }
  ul.fails .ok { color:#7fcf9f; }
  ul.fails .err { color:#e08a8a; }
</style>
</head>
<body>
<header>
  <h1>qtgrace 示例复刻画廊</h1>
  <div class="meta">目录: %(exdir)s · 格式: %(fmt)s · 成功 %(nrendered)d / 共 %(ntotal)d 个 .agr</div>
</header>
<main>
  <section>
    <h2>已复刻 (%(nrendered)d)</h2>
    <div class="grid">
%(okgrid)s
    </div>
  </section>
  <section>
    <h2>未复刻 / 失败 (%(nfail)d)</h2>
    <ul class="fails">
%(failrows)s
    </ul>
  </section>
</main>
</body>
</html>
'''


def build_gallery_html(rendered, failed, outdir, examples_dir, fmt="png") -> str:
    """生成一个自包含的 HTML 画廊, 展示成功复刻的示例。"""
    items = []
    for rec in rendered:
        base = os.path.basename(rec["output"])
        size_kb = (rec.get("size", 0) + 512) // 1024
        items.append(
            '      <figure>\n'
            '        <a href="%s" target="_blank">'
            '<img loading="lazy" src="%s" alt="%s"></a>\n'
            '        <figcaption>%s <span class="sz">%d KB</span></figcaption>\n'
            '      </figure>' % (base, base, rec["name"], rec["name"], size_kb))
    ok_grid = "\n".join(items) if items else '      <p class="empty">无成功渲染的示例</p>'
    if failed:
        fail_rows = "\n".join(
            '          <li>%s — <span class="err">%s</span></li>'
            % (r["name"], r["error"]) for r in failed)
    else:
        fail_rows = '          <li class="ok">无</li>'
    ntotal = len(rendered) + len(failed)
    html = GALLERY_TEMPLATE % {
        "exdir": examples_dir,
        "fmt": (fmt or "png").upper(),
        "nrendered": len(rendered),
        "nfail": len(failed),
        "ntotal": ntotal,
        "okgrid": ok_grid,
        "failrows": fail_rows,
    }
    path = os.path.join(outdir, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


# =====================================================================
# MCP Server (mcp >= 2.0)
# =====================================================================
if HAVE_MCP:
    mcp = FastMCP(
        name="qtgrace-mcp",
        instructions="通过 TCP 控制 qtgrace 科学绘图软件的 MCP 服务器",
    )

    _controller: Optional[QtGraceController] = None
    _batch: Optional[QtGraceBatchRenderer] = None
    _global_lock = threading.Lock()


    def _get_controller(qtgrace_path: Optional[str] = None,
                        workdir: Optional[str] = None) -> QtGraceController:
        global _controller
        with _global_lock:
            if _controller is None or not _controller.is_running():
                _controller = QtGraceController(
                    qtgrace_path or DEFAULT_QTGRACE, workdir)
            return _controller


    def _get_batch(qtgrace_path: Optional[str] = None) -> QtGraceBatchRenderer:
        global _batch
        with _global_lock:
            if _batch is None:
                _batch = QtGraceBatchRenderer(qtgrace_path or DEFAULT_QTGRACE)
            return _batch


    @mcp.tool()
    def start_qtgrace(qtgrace_path: str = DEFAULT_QTGRACE, workdir: str = "",
                      device: str = "PNG") -> str:
        """启动 qtgrace 进程并建立 TCP 控制通道。返回运行状态。

        device: 输出设备 PNG/EPS/PS/SVG/JPEG/BMP/TIFF (决定导出格式)。
        """
        ctrl = _get_controller(qtgrace_path, workdir or None)
        return ctrl.start(device)


    @mcp.tool()
    def plot_data(datasets: list, title: str = "", xlabel: str = "", ylabel: str = "",
                  legends: Optional[list] = None, style: str = "",
                  set_type: str = "xy",
                  xmin: Optional[float] = None, xmax: Optional[float] = None,
                  ymin: Optional[float] = None, ymax: Optional[float] = None) -> str:
        """向 qtgrace 发送数据并绘图。

        datasets: [[col1_list, col2_list, ...], ...] 每个数据集是列的列表。
                  xy/bar 为 [[x,y], ...] (2列); xydy 为 [[x,y,dy], ...] (3列)。
        set_type: 图类型 xy/xydy/xydxdy/bar/bardy/xyboxplot 等 (默认 xy)。
                  TCP 模式仅支持 2 列 (xy/bar), 多列类型请用 plot_and_export(use_batch=True)。
        title/xlabel/ylabel: 可选标签。legends: 每条曲线的图例。
        style: 可选样式。支持预设名 journal/minimal/dark/presentation/grid,
               或自定义 Grace 命令 (如 '@s0 line color 2'),
               也可叠加 'journal+@s0 line color 2'。
        xmin/xmax/ymin/ymax: 可选, 显式设置坐标范围。
        """
        set_type = (set_type or "xy").lower()
        expected_cols = SET_TYPE_COLS.get(set_type)
        if expected_cols is None:
            raise QtGraceError(
                f"未知 set_type: {set_type}. 支持: {', '.join(sorted(SET_TYPE_COLS))}")
        parsed = []
        for ds in datasets:
            if len(ds) != expected_cols:
                raise QtGraceError(
                    f"set_type={set_type} 每个数据集需 {expected_cols} 列, 得到 {len(ds)} 列")
            parsed.append([list(col) for col in ds])
        ctrl = _get_controller()
        return ctrl.plot(parsed, title, xlabel, ylabel, legends, style or None,
                         set_type, xmin, xmax, ymin, ymax)


    @mcp.tool()
    def export_image(filename: str = "plot", format: str = "png") -> str:
        """导出当前图为图像文件。返回输出文件绝对路径。

        filename: 输出文件名(不含扩展名或含)。绝对路径亦可。
        format: png / ps / eps / pdf / svg (取决于 qtgrace 设备支持)。
        """
        ctrl = _get_controller()
        return ctrl.export(filename, format)


    @mcp.tool()
    def qtgrace_exec(command: str, redraw: bool = True) -> str:
        """向运行中的 qtgrace 发送原始 Grace 命令/脚本, 解锁全部高级绘图能力。

        通过 TCP ServerMode 的 PLOT_INFO 通道发送任意 Grace 命令, 适用于交互式/
        可视化驱动 (GUI 实时可见)。例如切换图类型、对数轴、标注/箭头、填充、
        图例控制、多图布局等——这些无法通过数据接口 plot_data 表达的能力都可用此工具。

        command: 一行或多行 Grace 命令, 多行用 \\n 分隔。也可传整段 .agr 片段。
                 例: '@g0 type Chart\\n@g0 stacked true\\n@s0 type bar'
                     '@ xaxes scale Logarithmic'   (x 轴对数)
                     '@with string\\n@string on\\n@string def "peak"'  (文本标注)
        redraw: 发送后是否重绘 GUI (默认 True)。

        重要限制 (qtgrace v026 环境):
         - 命令发送本身可靠; 但 Chart/stacked/Polar/Pie 等高级图类型的【无头导出】
           (接 export_image) 会挂起——这类图请在 GUI 中查看, 不要导出。
         - 仅 XY / 基础 bar 类型可经 export_image 无头导出。
         - 若 qtgrace 未运行, 本工具会自动启动一个实例。
        """
        if not command or not command.strip():
            return "未提供命令"
        ctrl = _get_controller()
        if not ctrl.is_running():
            return "qtgrace 未运行且无法启动, 请先调用 start_qtgrace()"
        try:
            parts = []
            n = 0
            for line in command.replace("\r\n", "\n").split("\n"):
                if line.strip():
                    parts.append(ctrl._plot_info(line + "\n"))
                    n += 1
            if redraw:
                parts.append(ctrl._cmd(REDRAW))
            stream = b"".join(parts)
            ctrl._session(lambda sr, sw: ctrl._send_stream(sr, sw, stream))
            return f"已发送 {n} 条 Grace 命令" + (" 并重绘 GUI" if redraw else "")
        except QtGraceError as e:
            return f"命令发送失败: {e}"
        except Exception as e:  # 网络/超时等
            return f"命令发送异常: {e}"


    @mcp.tool()
    def plot_and_export(datasets: list, filename: str = "plot", title: str = "",
                        xlabel: str = "", ylabel: str = "",
                        legends: Optional[list] = None,
                        style: str = "", format: str = "png",
                        use_batch: bool = False,
                        set_type: str = "xy",
                        xmin: Optional[float] = None, xmax: Optional[float] = None,
                        ymin: Optional[float] = None, ymax: Optional[float] = None) -> str:
        """一步完成: 绘图 + 导出图像。返回输出文件绝对路径。

        set_type: 图类型 xy/xydy/xydxdy/bar/bardy/xyboxplot 等。
                  多列类型 (xydy 等) 自动切换到 batch 模式 (无需手动 use_batch=True)。
        use_batch=True 时使用 -batch 回退模式 (无需保持 GUI 进程, 但每次独立渲染)。
        style: 可选样式。支持预设名 journal/minimal/dark/presentation/grid,
               或自定义 Grace 命令, 也可叠加 'journal+@s0 line color 2'。
        xmin/xmax/ymin/ymax: 可选, 显式设置坐标范围。
        """
        set_type = (set_type or "xy").lower()
        expected_cols = SET_TYPE_COLS.get(set_type, 2)
        # 多列类型必须走 batch (TCP 仅支持 2 列)
        if expected_cols > 2 and not use_batch:
            logger.info("set_type=%s 需要 %d 列, 自动切换到 batch 模式",
                        set_type, expected_cols)
            use_batch = True
        # 验证数据列数
        for ds in datasets:
            if len(ds) != expected_cols:
                raise QtGraceError(
                    f"set_type={set_type} 每个数据集需 {expected_cols} 列, 得到 {len(ds)} 列")
        parsed = [[list(col) for col in ds] for ds in datasets]

        if use_batch:
            renderer = _get_batch()
            return renderer.render(parsed, filename, title, xlabel, ylabel,
                                   legends, format, set_type, style or None,
                                   xmin, xmax, ymin, ymax)
        ctrl = _get_controller()
        # 若目标格式与当前设备不符, 自动重启 qtgrace 到对应设备
        target = DEVICE_MAP.get((format or "png").lower().lstrip("."), "PNG")
        if ctrl.device != target:
            ctrl.stop()
            ctrl.start(target)
        ctrl.plot(parsed, title, xlabel, ylabel, legends, style or None,
                  set_type, xmin, xmax, ymin, ymax)
        return ctrl.export(filename, format)


    @mcp.tool()
    def set_scaling_mode(mode: int = 1, graph_no: int = 0,
                         x_start: float = 0.0, x_end: float = 0.0) -> str:
        """设置坐标缩放模式。

        mode: 0=不动, 1=自动缩放所有轴(默认), 2=只自动缩放 y 轴(需 x_start/x_end)。
        """
        ctrl = _get_controller()
        return ctrl.set_scaling_mode(mode, graph_no, x_start, x_end)


    @mcp.tool()
    def set_layout_mode(layout: int = 0, graph_no: int = 0,
                        cols: int = 1, num_graphs: int = 1) -> str:
        """设置图布局模式。

        layout: 0=默认, 1=join(多图合并), 2=overlay(叠加), 3=排列(cols×rows)。
        """
        ctrl = _get_controller()
        return ctrl.set_layout_mode(layout, graph_no, cols, num_graphs)


    @mcp.tool()
    def save_agr(filename: str = "plot.agr") -> str:
        """保存当前 qtgrace 项目为 .agr 参数文件。返回文件绝对路径。

        filename: 输出路径(绝对路径或相对名, 自动加 .agr)。
        """
        ctrl = _get_controller()
        if not filename.endswith(".agr"):
            filename += ".agr"
        return ctrl.save_agr(filename)


    @mcp.tool()
    def stop_qtgrace() -> str:
        """关闭 qtgrace 进程。"""
        global _controller
        if _controller is None:
            return "qtgrace 未启动"
        return _controller.stop()


    @mcp.tool()
    def list_examples(examples_dir: str = "") -> str:
        """列出 qtgrace 官方示例目录中的可复刻文件。

        分类列出 .agr(项目)/.par(参数)/.com(命令)/.fit(拟合)/.cc/.cpp/.h 等。
        examples_dir: 示例目录(默认 D:\\qtgrace_v026_Win7\\examples)。
        """
        info = list_examples_dir(examples_dir or None)
        if not info["exists"]:
            raise QtGraceError(f"示例目录不存在: {info['dir']}")
        lines = [f"示例目录: {info['dir']}", ""]
        for ext, items in info["categories"].items():
            desc = EXAMPLE_CATEGORIES.get(ext, "")
            lines.append(f"== {ext} ({len(items)}) {desc}")
            for it in items:
                lines.append(f"  {it}")
            lines.append("")
        return "\n".join(lines).strip()


    @mcp.tool()
    def render_example(examples_dir: str = "", name: str = "",
                       format: str = "png", outdir: str = "") -> str:
        """复刻(渲染)单个 qtgrace 示例 .agr 为图像文件。

        name: 示例文件名(可带或不带 .agr 后缀), 如 'bar' 或 'bar.agr'。
        format: png/eps/ps/svg/jpg/bmp/tif/pdf/xpm (默认 png)。
        outdir: 输出目录(默认 examples_dir/rendered)。
        返回输出路径或失败原因 (部分图类型在 qtgrace v026 会崩溃, 属已知限制)。
        """
        if not name:
            raise QtGraceError("请提供 name (示例文件名)")
        rec = render_example_file(examples_dir or None, name, format,
                                  outdir or None)
        if rec["status"] == "ok":
            return (f"已复刻: {rec['name']} -> {rec['output']} "
                    f"({rec.get('size', 0)} bytes)")
        return f"复刻失败: {rec['name']}: {rec['error']}"


    @mcp.tool()
    def render_all_examples(examples_dir: str = "", format: str = "png",
                            outdir: str = "", build_gallery: bool = True) -> str:
        """批量复刻示例目录中所有 .agr, 返回逐文件报告, 可选生成 HTML 画廊。

        build_gallery=True 时生成 index.html (在 outdir 下), 用浏览器打开即可浏览。
        已知 qtgrace v026 hardcopy 对 charts/logit/pie/terr/tstack/txyr/xyz
        会 segfault, 这些会被标记为 failed 而非阻塞其余示例。
        """
        report = render_examples_dir(examples_dir or None, format,
                                     outdir or None, build_gallery)
        lines = [
            f"示例目录: {report['examples_dir']}",
            f"输出目录: {report['outdir']}",
            f"格式: {report['format']}",
            f"总计 {report['total']} 个 .agr · 成功 {len(report['rendered'])} · "
            f"失败 {len(report['failed'])}",
            "",
            "成功:",
        ]
        for r in report["rendered"]:
            lines.append(f"  [OK]   {r['name']}")
        if report["failed"]:
            lines.append("失败:")
            for r in report["failed"]:
                lines.append(f"  [FAIL] {r['name']} — {r['error']}")
        if report["gallery"]:
            lines.append("")
            lines.append(f"画廊: {report['gallery']}")
        return "\n".join(lines)


    @mcp.tool()
    def qtgrace_status() -> str:
        """查询 qtgrace 运行状态。"""
        global _controller
        if _controller is None or not _controller.is_running():
            return "qtgrace 未运行"
        return (f"qtgrace 运行中 (pid={_controller.proc.pid}, "
                f"write_port={_controller.write_port}, "
                f"read_port={_controller.read_port}, "
                f"workdir={_controller.workdir})")


    # =================================================================
    # 批处理引擎 (batch.py) + 能力目录 (catalog.py)
    #
    # 与上面的 TCP 工具互补: TCP 驱动运行中的 GUI, 这里是无 GUI 的确定性出图。
    # 两条路共用同一份 .agr 生成器与打印机保护。
    # =================================================================

    @mcp.tool()
    def qtgrace_reference(section: str = "overview") -> str:
        """【画图前先读】qtgrace 能力目录 —— 有哪些图能画、参数填什么。

        返回机器可读的完整能力清单 (从 qtgrace 源码与 examples 逆向得出)。

        section 可选:
          overview     三步工作流 + 「你要画什么→用哪个 graph_type」对照表
          graph_types  XY / CHART / POLAR / POLARV2 / PIE
          set_types    全部 19 种数据集类型及列顺序 (xydy=[x,y,err] 等)
          symbols      符号代码 0-11
          line_types   线型代码 0-7
          axes         刻度格式、刻度朝向、轴缩放 (normal/log/reciprocal/logit)
          colors       调色板序号→名称→RGB
          fonts        字体序号→PostScript 名
          expressions  表达式内置函数
          devices      输出格式及哪些真的可用
          advanced     fill / baseline / dropline / avalue / 零轴 / 对侧轴 /
                       标注 (文字/线/箭头/椭圆/矩形) / 时间戳 / frame /
                       autoscale / worldStack / znorm / defaults
          silent_traps 写错不报错、只是静默不生效的坑
          recipes      可直接照搬的完整 make_plot 调用
          limitations  本构建的硬限制
          all          全部
        """
        import json
        from . import catalog
        return json.dumps(catalog.get_section(section), ensure_ascii=False, indent=2)

    @mcp.tool()
    def make_plot(spec_json: str, save_agr: bool = False,
                  device: str = "PNG", timeout_ms: int = 60000) -> str:
        """【主力工具】由结构化数据从零生成 .agr 并渲染出图。

        与 plot_data 的区别: plot_data 经 TCP 驱动运行中的 GUI; make_plot 直接
        生成 .agr 文件再调 -hardcopy 渲染, 不需要 GUI 进程, 且能表达 plot_data
        覆盖不到的高级图形 (Chart/Polar/Pie/对数轴/填充/标注)。

        spec_json: JSON 字符串, 结构如下 (详见 qtgrace_reference):
        {
          "output_path": "D:/figs/a.png",
          "width_cm": 8.6, "height_cm": 6.4,
          "description": "可选工程说明",
          "timestamp": {"format": "run 42"},            // 可选
          "annotations": [{"type":"text","x":0.1,"y":0.9,"text":"hi","graph":0},
                          {"type":"arrow","x1":0,"y1":0,"x2":1,"y2":1,"graph":0}],
          "graphs": [{
            "graph_type": "XY",                          // XY/CHART/POLAR/POLARV2/PIE
            "title": "标题", "world": [xmin,ymin,xmax,ymax],
            "xaxis": {"label":"x","scale":"log","tickMajor":1,"zero":true,"alt":true},
            "yaxis": {"label":"y","tickMajor":0.5},
            "legend": {"pos":[0.6,0.8]},
            "sets": [{"type":"xydy","legend":"L","lineType":1,"lineColor":2,
                      "fill":{"type":2,"color":7},
                      "avalue":{"type":2,"prec":2},
                      "data":[[x,y,yerr], ...]}]
          }]
        }

        颜色用调色板序号 (1黑 2红 3绿 4蓝 5黄 11橙), 不是十六进制。
        建议显式给 world, 否则每次自动缩放结果可能不同。

        save_agr=True 时同时写出 .agr 工程文件 (与图片同名同目录), 可在 GUI 里
        打开手工微调 —— 这是「AI 出图 + 人工调位置」的工作流。

        PNG/JPEG 若因 qtgrace 段错误失败, 会自动改用 PNM 渲染再转换, 所以位图
        始终能出。矢量输出 (SVG/EPS/PS) 更稳, 投稿优先用它们。
        """
        import json as _json
        from .agr import build_agr
        from .batch import render as _render

        try:
            spec = _json.loads(spec_json) if isinstance(spec_json, str) else dict(spec_json)
        except _json.JSONDecodeError as exc:
            return f"spec_json 不是合法 JSON: {exc}"

        if not spec.get("output_path"):
            return "spec 缺少 output_path"
        try:
            agr = build_agr(spec)
        except ValueError as exc:
            return (f"参数错误: {exc}\n"
                    f"调用 qtgrace_reference(section='graph_types'/'set_types') 查合法取值")

        try:
            r = _render(agr_text=agr,
                        device=device or spec.get("device", "PNG"),
                        output_path=spec["output_path"],
                        timeout_ms=timeout_ms,
                        save_agr=save_agr)
        except Exception as exc:  # noqa: BLE001
            return f"渲染失败: {exc}"

        if not r.get("ok"):
            return f"渲染失败: {r.get('error')}\n{r.get('hint','')}"

        size = (f"{r.get('width')}x{r.get('height')}"
                if r.get("width") else f"{r.get('bytes')} bytes")
        out = [f"出图成功: {r['output_path']}  ({r['device']}, {size})"]
        if r.get("agr_path"):
            out.append(f"工程文件: {r['agr_path']}  (可在 qtgrace GUI 里打开微调)")
        if r.get("bitmap_fallback"):
            out.append(f"注意: {r['bitmap_fallback']}")
        return "\n".join(out)

    @mcp.tool()
    def describe_agr(agr_text: str = "", agr_path: str = "") -> str:
        """解析检查一份 .agr: 语句数、图/数据集、数据行, 以及本构建已知会拒绝的写法。

        改完 .agr 想先确认没问题再渲染时用。
        """
        import re
        path = agr_path
        if not agr_text and path:
            if not os.path.isfile(path):
                return f"文件不存在: {path}"
            with open(path, encoding="utf-8", errors="replace") as fh:
                agr_text = fh.read()
        if not agr_text:
            return "请提供 agr_text 或 agr_path"

        lines = agr_text.splitlines()
        graphs, sets, rows = set(), set(), 0
        for raw in lines:
            s = raw.strip()
            if s.startswith("@with g"):
                graphs.add(s.split("g", 1)[1].strip())
            elif s.startswith("@target g") and "." in s:
                g, st = s.split(None, 1)[1].split(".", 1)
                graphs.add(g.lstrip("g"))
                sets.add(f"{g}.{st}")
            elif s and not s.startswith("#") and s != "&":
                rows += 1

        warns = []
        inline = [ln for ln in lines if ln.strip().startswith("@") and "#" in ln]
        if inline:
            warns.append(f"{len(inline)} 行含行内 # 注释 —— 本构建会整行判语法错误")
        for ln in lines:
            m = re.match(r"@\s*with\s+(line|box|ellipse)\s*$", ln)
            if m:
                kw = m.group(1)
                if not any(re.match(rf"@{kw}\s+def\s*$", x) for x in lines):
                    warns.append(f"有 @with {kw} 块但缺少顶格的 @{kw} def —— 该标注不会画出来")
        return "\n".join([
            f"行数      : {len(lines)}",
            f"语句数    : {sum(1 for ln in lines if ln.strip().startswith('@'))}",
            f"图        : {sorted(graphs) or '(无)'}",
            f"数据集    : {len(sets)}",
            f"数据行    : {rows}",
            f"有 @version: {'是' if any(ln.strip().startswith('@version') for ln in lines) else '否'}",
        ] + (["警告:"] + [f"  - {w}" for w in warns] if warns else []))

    @mcp.tool()
    def qtgrace_capabilities() -> str:
        """环境体检: qtgrace 路径、能否出图、打印机风险、输出设备。

        画图前或排查失败时先看这个。特别是 printer_risky —— 默认打印机若是不
        可达的网络打印机, qtgrace 的 -hardcopy 会弹模态框「正在等待打印机连接…」
        并永久阻塞 (这是最常见的"卡住不出图"原因)。
        """
        import json
        from .batch import probe, resolve_exe, resolve_home
        from .printer import diagnose

        exe = resolve_exe()
        info = {
            "executable": exe,
            "executable_present": os.path.isfile(exe),
            "grace_home": resolve_home(exe),
        }
        try:
            info["printer"] = diagnose()
            info["printer_risky"] = bool(info["printer"].get("risky"))
        except Exception as exc:  # noqa: BLE001
            info["printer_error"] = str(exc)
        try:
            h = probe()
            info["rendering_available"] = h["ok"]
            info["rendering_note"] = h["reason"]
        except Exception as exc:  # noqa: BLE001
            info["rendering_note"] = str(exc)
        return json.dumps(info, ensure_ascii=False, indent=2)


def main() -> None:
    """入口: 以 stdio 传输启动 MCP 服务器。"""
    if not HAVE_MCP:
        print('需要安装 mcp SDK: pip install "mcp[cli]"', file=sys.stderr)
        sys.exit(1)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()


