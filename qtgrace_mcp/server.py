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
"""
from __future__ import annotations

import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Optional

try:
    from mcp.server.fastmcp import FastMCP
    HAVE_MCP = True
except ImportError:
    HAVE_MCP = False

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

def _default_qtgrace_path() -> str:
    """探测 qtgrace 可执行文件路径。

    优先级: 环境变量 QTGRACE_PATH > 常见安装位置。
    """
    env = os.environ.get('QTGRACE_PATH')
    if env:
        return env
    candidates = [
        r'D:\qtgrace_v026_Win7\bin\qtgrace.exe',
        r'C:\qtgrace\bin\qtgrace.exe',
        r'C:\Program Files\qtgrace\bin\qtgrace.exe',
        '/usr/local/bin/qtgrace',
        '/usr/bin/qtgrace',
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


DEFAULT_QTGRACE = _default_qtgrace_path()


class QtGraceError(RuntimeError):
    pass


class QtGraceController:
    """qtgrace 进程 + TCP 控制封装。"""

    def __init__(self, qtgrace_path: str = DEFAULT_QTGRACE, workdir: Optional[str] = None):
        self.path = os.path.abspath(qtgrace_path)
        if not os.path.exists(self.path):
            raise QtGraceError(f'qtgrace 可执行文件不存在: {self.path}')
        self.workdir = workdir or os.path.join(tempfile.gettempdir(), 'qtgrace_mcp')
        os.makedirs(self.workdir, exist_ok=True)
        self.proc = None
        self.write_port = None
        self.read_port = None
        self._sock_w = None
        self._sock_r = None
        self.export_name = None
        self.device = 'PNG'  # 当前输出设备

    # ---------- 进程管理 ----------
    def start(self, device: str = 'PNG') -> str:
        """启动 qtgrace。device: PNG/EPS/PS/SVG/JPEG/BMP 等输出设备。"""
        if self.proc and self.proc.poll() is None:
            return (f'qtgrace 已在运行 (write={self.write_port}, read={self.read_port}, '
                    f'device={self.device})')
        device = (device or 'PNG').upper().lstrip('.')
        # 随机选空闲端口
        with socket.socket() as s1, socket.socket() as s2:
            s1.bind(('127.0.0.1', 0))
            s2.bind(('127.0.0.1', 0))
            self.write_port = s1.getsockname()[1]
            self.read_port = s2.getsockname()[1]
        env = dict(os.environ)
        # 避免首次运行的设置向导阻塞
        env.setdefault('QTGRACE_NO_WIZARD', '1')
        args = [self.path, '-ServerMode', str(self.write_port), str(self.read_port)]
        if device != 'PNG':
            args += ['-hdevice', device]
        self.device = device
        self.proc = subprocess.Popen(
            args,
            cwd=self.workdir, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # 等待端口就绪
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise QtGraceError(f'qtgrace 启动即退出, code={self.proc.returncode}')
            try:
                with socket.create_connection(('127.0.0.1', self.write_port), timeout=0.5):
                    with socket.create_connection(('127.0.0.1', self.read_port), timeout=0.5):
                        break
            except OSError:
                time.sleep(0.3)
        else:
            raise QtGraceError('qtgrace TCP 端口 10 秒内未就绪')
        return (f'qtgrace 已启动 (pid={self.proc.pid}, write={self.write_port}, '
                f'read={self.read_port}, device={self.device}, workdir={self.workdir})')

    def stop(self) -> str:
        if not self.proc or self.proc.poll() is not None:
            self._cleanup_sockets()
            return 'qtgrace 未在运行'
        try:
            self._session(lambda sr, sw: self._send_stream(sr, sw, struct.pack('i', KILL_CHILD)))
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._cleanup_sockets()
        return 'qtgrace 已关闭'

    def is_running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def _cleanup_sockets(self):
        for s in (self._sock_w, self._sock_r):
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        self._sock_w = self._sock_r = None

    # ---------- TCP 会话 ----------
    def _connect(self):
        if not self.is_running():
            raise QtGraceError('qtgrace 未运行, 请先调用 start_qtgrace')
        sr = socket.create_connection(('127.0.0.1', self.read_port), timeout=5)
        sw = socket.create_connection(('127.0.0.1', self.write_port), timeout=5)
        sr.settimeout(15)
        sw.settimeout(15)
        sr.sendall(struct.pack('i', initComm))
        resp = struct.unpack('i', sw.recv(4))[0]
        if resp != initComm:
            sr.close(); sw.close()
            raise QtGraceError(f'握手失败: 期望 {initComm}, 得到 {resp}')
        return sr, sw

    @staticmethod
    def _send_stream(sr, sw, payload: bytes) -> int:
        sr.sendall(struct.pack('i', readDataComm))
        sr.sendall(struct.pack('i', len(payload)))
        sr.sendall(payload)
        resp = struct.unpack('i', sw.recv(4))[0]
        return resp

    def _session(self, fn):
        """新会话: 连接->执行->关闭。"""
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
        return struct.pack('i', c)

    @staticmethod
    def _plot_info(s: str) -> bytes:
        b = s.encode('utf-8')
        return struct.pack('i', PLOT_INFO) + struct.pack('i', len(b)) + b

    @staticmethod
    def _datavec(xs, ys) -> bytes:
        n = len(xs)
        if len(ys) != n:
            raise QtGraceError('x/y 长度不一致')
        out = struct.pack('i', WRITE_DATAVEC) + struct.pack('i', n)
        out += struct.pack('d' * n, *xs) + struct.pack('d' * n, *ys)
        return out

    @staticmethod
    def _ps_filename(name: str) -> bytes:
        b = name.encode('utf-8')
        return struct.pack('i', PS_FILENAME) + struct.pack('i', len(b)) + b

    # ---------- 高层操作 ----------
    def plot(self, datasets: list[tuple[list, list]],
             title: str = '', xlabel: str = '', ylabel: str = '',
             legends: Optional[list[str]] = None,
             style: Optional[str] = None,
             xmin: Optional[float] = None, xmax: Optional[float] = None,
             ymin: Optional[float] = None, ymax: Optional[float] = None) -> str:
        """发送数据并成图。datasets: [(x, y), ...]

        xmin/xmax/ymin/ymax: 可选, 显式设置世界坐标范围 (@world 命令)。
        """
        if not datasets:
            raise QtGraceError('没有数据')
        parts = [self._plot_info('@FLUSH\n'), self._plot_info('@ g0 on\n')]
        if title:
            parts.append(self._plot_info(f'@title "{title}"\n'))
        if xlabel:
            parts.append(self._plot_info(f'@xaxis label "{xlabel}"\n'))
        if ylabel:
            parts.append(self._plot_info(f'@yaxis label "{ylabel}"\n'))
        if xmin is not None:
            parts.append(self._plot_info(f'@world xmin {xmin}\n'))
        if xmax is not None:
            parts.append(self._plot_info(f'@world xmax {xmax}\n'))
        if ymin is not None:
            parts.append(self._plot_info(f'@world ymin {ymin}\n'))
        if ymax is not None:
            parts.append(self._plot_info(f'@world ymax {ymax}\n'))
        if legends:
            for i, lg in enumerate(legends):
                parts.append(self._plot_info(f'@s{i} legend "{lg}"\n'))
                parts.append(self._plot_info(f'@s{i} legend on\n'))
        if style:
            parts.append(self._plot_info(style if style.endswith('\n') else style + '\n'))
        for xs, ys in datasets:
            parts.append(self._datavec(xs, ys))
            parts.append(self._cmd(WRITE_DATAVEC_FINISHED))
        parts.append(self._cmd(REDRAW))
        parts.append(self._cmd(END_COMM))
        stream = b''.join(parts)

        def run(sr, sw):
            resp = self._send_stream(sr, sw, stream)
            if resp != endComm:
                raise QtGraceError(f'绘图失败: 响应 {resp}')
            return resp
        self._session(run)
        n = len(datasets)
        return f'已绘制 {n} 个数据集, 共 {sum(len(x) for x, _ in datasets)} 个点'

    def save_agr(self, filename: str) -> str:
        """通过 TCP 让 qtgrace 生成当前项目的 .agr 参数文本并保存。

        流程: 发 sendDataCom(3) -> qtgrace 回 [sendDataCom][doclen][docname][len][.agr文本]
        filename: 输出 .agr 路径(绝对路径或相对 workdir)。
        """
        if not self.is_running():
            raise QtGraceError('qtgrace 未运行')

        # 绝对路径 -> % 前缀
        if os.path.isabs(filename):
            save_name = '%' + filename
        else:
            save_name = filename
        fname_bytes = save_name.encode('utf-8')
        payload = struct.pack('i', PS_FILENAME) + struct.pack('i', len(fname_bytes)) + fname_bytes

        def run(sr, sw):
            # 1) 设置 docname (让 qtgrace 记住名字)
            sr.sendall(struct.pack('i', readDataComm))
            sr.sendall(struct.pack('i', len(payload)))
            sr.sendall(payload)
            r1 = struct.unpack('i', sw.recv(4))[0]
            if r1 != endComm:
                raise QtGraceError(f'设置 docname 失败: {r1}')
            # 2) 请求参数 (sendDataCom=3)
            sr.sendall(struct.pack('i', 3))  # sendDataCom
            # qtgrace 响应: [sendDataCom][bytesToSend][data][endComm]
            # data = [doclen][docname][paramLen][.agr文本]
            def recv_exact(n):
                buf = b''
                while len(buf) < n:
                    chunk = sw.recv(n - len(buf))
                    if not chunk:
                        raise QtGraceError('连接中断, 接收数据不完整')
                    buf += chunk
                return buf

            mode = struct.unpack('i', recv_exact(4))[0]
            if mode != 3:
                raise QtGraceError(f'预期 sendDataCom(3), 得到 {mode}')
            total = struct.unpack('i', recv_exact(4))[0]
            data = recv_exact(total)
            # 解析 data: [doclen][docname][paramLen][pp]
            off = 0
            dl = struct.unpack('i', data[off:off + 4])[0]
            off += 4
            off += dl  # docname
            pl = struct.unpack('i', data[off:off + 4])[0]
            off += 4
            agr_text = data[off:off + pl].decode('utf-8', errors='replace')
            # 尾部 endComm(1)
            return agr_text

        agr_text = self._session(run)

        # 保存
        if os.path.isabs(filename):
            out = filename
        else:
            out = os.path.join(self.workdir, filename)
        os.makedirs(os.path.dirname(out), exist_ok=True) if os.path.dirname(out) else None
        # 显式设置上/右轴(altxaxis/altyaxis)关闭: tick size 0 + 全 off
        alt_axis_block = (
            '@    altxaxis  off\n'
            '@    altxaxis  tick off\n'
            '@    altxaxis  tick major size 0\n'
            '@    altxaxis  tick minor size 0\n'
            '@    altxaxis  ticklabel off\n'
            '@    altxaxis  bar off\n'
            '@    altyaxis  off\n'
            '@    altyaxis  tick off\n'
            '@    altyaxis  tick major size 0\n'
            '@    altyaxis  tick minor size 0\n'
            '@    altyaxis  ticklabel off\n'
            '@    altyaxis  bar off\n'
        )
        # 移除已有的单行 altxaxis/altyaxis off/on 行, 替换为完整块
        import re as _re
        agr_text = _re.sub(r'@\s+alt[x]?axis\s+(on|off)\s*\n', '', agr_text)
        agr_text = _re.sub(r'@\s+alt[y]?axis\s+(on|off)\s*\n', '', agr_text)
        # 插入到 @with g0 之后 (altxaxis 是 g0 的属性, 必须在 @with g0 作用域内)
        marker = '@with g0'
        if marker in agr_text:
            idx = agr_text.find(marker)
            nl = agr_text.find('\n', idx)
            insert_at = nl + 1 if nl != -1 else len(agr_text)
            agr_text = agr_text[:insert_at] + alt_axis_block + agr_text[insert_at:]
        else:
            agr_text = agr_text.rstrip() + '\n\n' + alt_axis_block
        with open(out, 'w', encoding='utf-8') as f:
            f.write(agr_text)
        return out

    def set_scaling_mode(self, mode: int = 1, graph_no: int = 0,
                         x_start: float = 0.0, x_end: float = 0.0) -> str:
        """设置缩放模式 (命令 6, 对应官方 sendScalingModeInfo)。

        mode: 0=DEFAULT_LAYOUT(不动), 1=AUTOSCALE_ALL_AXES(默认),
              2=AUTOSCALE_Y_AXIS_OR_OVERLAY(只缩放 y, 用 x_start/x_end)
        """
        # 数据流: [cmd 6][graphNo][mode][xStart][xEnd]
        payload = struct.pack('i', SET_SCALING_MODE)
        payload += struct.pack('i', graph_no)
        payload += struct.pack('i', mode)
        if mode == 2:
            payload += struct.pack('d', x_start) + struct.pack('d', x_end)
        resp = self._session(lambda sr, sw: self._send_stream(sr, sw, payload))
        if resp != endComm:
            raise QtGraceError(f'SET_SCALING_MODE 失败: {resp}')
        return f'scaling mode={mode} graph={graph_no}'

    def set_layout_mode(self, layout: int = 0, graph_no: int = 0,
                        cols: int = 1, num_graphs: int = 1) -> str:
        """设置图布局 (命令 8, 对应官方 sendLayoutModeInfo)。

        layout: 0=DEFAULT, 1=AUTOSCALE_ALL_AXES_OR_JOIN_PLOT(join),
                2=AUTOSCALE_Y_AXIS_OR_OVERLAY(overlay), 3=GRAPH_POSITION(排列)
        """
        payload = struct.pack('i', SET_LAYOUT_MODE)
        payload += struct.pack('i', graph_no)
        payload += struct.pack('i', layout)
        if layout == 3:
            payload += struct.pack('i', cols) + struct.pack('i', num_graphs)
        resp = self._session(lambda sr, sw: self._send_stream(sr, sw, payload))
        if resp != endComm:
            raise QtGraceError(f'SET_LAYOUT_MODE 失败: {resp}')
        return f'layout={layout} graph={graph_no}'

    def plot_and_export(self, datasets, filename='plot', title='', xlabel='', ylabel='',
                        legends=None, style=None, format='png',
                        xmin=None, xmax=None, ymin=None, ymax=None) -> str:
        """plot() + export() 一步完成。"""
        self.plot(datasets, title, xlabel, ylabel, legends, style,
                  xmin, xmax, ymin, ymax)
        return self.export(filename, format)

    def export(self, filename: str, format: str = 'png') -> str:
        """设置导出名并导出图像。返回输出文件绝对路径。

        格式由启动时的 -hdevice 决定; 若 format 与当前设备不符,
        自动重启 qtgrace 到对应设备 (需重新 plot)。
        注意: 文件名须为相对路径(写入 workdir)或 %绝对路径。
        """
        fmt = (format or 'png').lower().lstrip('.')
        dev_map = {
            'png': 'PNG', 'ps': 'PS', 'eps': 'EPS', 'svg': 'SVG',
            'jpg': 'JPEG', 'jpeg': 'JPEG', 'bmp': 'BMP', 'tif': 'TIFF',
            'tiff': 'TIFF', 'xpm': 'XPM', 'pdf': 'PDF',
        }
        target_dev = dev_map.get(fmt, 'PNG')
        if self.device != target_dev:
            raise QtGraceError(
                f'当前设备 {self.device} 不支持导出 {fmt}。'
                f'请用 start_qtgrace(device="{target_dev}") 重启 qtgrace 后重新绘图导出。'
                f'支持格式: {", ".join(sorted(dev_map))}')
        base = filename if filename else 'plot'
        # 无扩展名则补默认
        if '.' not in os.path.basename(base):
            base = f'{base}.{fmt}'
        # 绝对路径 -> 加 % 前缀 (qtgrace 约定)
        psname = base
        if os.path.isabs(base):
            psname = '%' + base

        def run(sr, sw):
            r1 = self._send_stream(sr, sw, self._ps_filename(psname))
            if r1 != endComm:
                raise QtGraceError(f'设置导出名失败: {r1}')
            r2 = self._send_stream(sr, sw, self._cmd(REDRAW_AND_WRITEPS))
            if r2 != endComm:
                raise QtGraceError(f'导出失败: {r2}')
            return r2
        self._session(run)

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
            raise QtGraceError(f'导出文件未生成: {out}')
        # 后处理: 清除 qtgrace 渲染 bug 产生的绘图框外顶部残留刻度线 (仅 PNG)
        if fmt == 'png':
            try:
                self._clean_top_ticks(out)
            except Exception:
                pass  # 后处理失败不影响导出
        return out

    @staticmethod
    def _clean_top_ticks(png_path: str):
        """清除绘图框上边界之上 12px 内的深色像素 (qtgrace 多曲线渲染 bug 的残留刻度线)。

        保留绘图框上边线 (top 本身), 只涂白 top-12..top-1 区域。
        数据曲线和标题都在框内或下方, 不受影响。
        """
        try:
            from PIL import Image
        except ImportError:
            return
        im = Image.open(png_path).convert('RGB')
        w, h = im.size
        px = im.load()
        # 统计每行/列深色像素
        col_dark = [0] * w
        row_dark = [0] * h
        for y in range(h):
            for x in range(w):
                r, g, b = px[x, y][:3]
                if r + g + b < 450:  # 深色
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
        # 涂白 top-12..top-1, x 范围含边距 (left-15..right+15)
        x0 = max(0, left - 15)
        x1 = min(w, right + 15)
        for y in range(y0, top):
            for x in range(x0, x1):
                px[x, y] = (255, 255, 255)
        im.save(png_path)


def build_batch_agr(datasets, title='', xlabel='', ylabel='', legends=None) -> str:
    """回退模式: 生成 .agr 批处理脚本文本 (数据部分; 导出由 -hardcopy 参数控制)。"""
    lines = ['# Grace project file (auto-generated by qtgrace-mcp)', '@version 50002',
             '@page size 792, 612', '@with g0', '@world xmin 0', '@world ymin 0']
    if title:
        lines.append(f'@title "{title}"')
    if xlabel:
        lines.append(f'@xaxis label "{xlabel}"')
    if ylabel:
        lines.append(f'@yaxis label "{ylabel}"')
    for i, (xs, ys) in enumerate(datasets):
        lines.append(f'@s{i} legend "{legends[i] if legends and i < len(legends) else ""}"')
        lines.append(f'@s{i} type xy')
        lines.append(f'@s{i} point type 0')
        lines.append(f'@s{i} line type 1')
        for x, y in zip(xs, ys):
            lines.append(f'{x:.10g} {y:.10g}')
    lines.append('@ autoscale onread none')
    return '\n'.join(lines) + '\n'


class QtGraceBatchRenderer:
    """回退模式: -batch 渲染 (无 TCP). 每次调用新进程, 无实时 GUI 控制。"""

    def __init__(self, qtgrace_path: str = DEFAULT_QTGRACE, workdir: Optional[str] = None):
        self.path = os.path.abspath(qtgrace_path)
        self.workdir = workdir or os.path.join(tempfile.gettempdir(), 'qtgrace_mcp_batch')
        os.makedirs(self.workdir, exist_ok=True)

    def render_agr(self, agr_path: str, out_base: str, fmt: str = 'png') -> str:
        """从已有 .agr 文件渲染导出图像 (保留 .agr 内全部样式)。

        agr_path: 输入 .agr 绝对路径
        out_base: 输出文件基础路径(无扩展名)
        """
        fmt = (fmt or 'png').lstrip('.')
        out = f'{out_base}.{fmt}'
        os.makedirs(os.path.dirname(out), exist_ok=True) if os.path.dirname(out) else None
        # -printfile 直接传绝对路径 (命令行参数不需要 % 前缀, 与 TCP PS_FILENAME 不同)
        proc = subprocess.Popen(
            [self.path, '-hardcopy', '-hdevice', fmt.upper(),
             '-printfile', out, '-batch', agr_path,
             '-nosigcatch', '-noask'],
            cwd=self.workdir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise QtGraceError('batch 渲染超时')
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
        raise QtGraceError(f'batch 渲染失败, 未生成 {out}')

    def render(self, datasets, filename, title='', xlabel='', ylabel='', legends=None, fmt='png'):
        agr = build_batch_agr(datasets, title, xlabel, ylabel, legends)
        tag = uuid.uuid4().hex[:8]
        agr_path = os.path.join(self.workdir, f'plot_{tag}.agr')
        fmt = (fmt or 'png').lstrip('.')
        out = os.path.join(self.workdir, f'{os.path.basename(filename or "plot")}.{fmt}')
        with open(agr_path, 'w', encoding='utf-8') as f:
            f.write(agr)
        proc = subprocess.Popen(
            # -hardcopy: gracebat 模式, 加载后自动导出并退出(无 GUI 事件循环)
            # -hdevice: 指定输出设备; -printfile: 输出文件; -noask: 防覆盖对话框挂起
            [self.path, '-hardcopy', '-hdevice', fmt.upper(),
             '-printfile', out,
             '-batch', agr_path,
             '-nosigcatch', '-noask'],
            cwd=self.workdir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise QtGraceError('batch 渲染超时')
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
        raise QtGraceError(f'batch 渲染失败, 未生成 {out}')


# =====================================================================
# MCP Server
# =====================================================================
if HAVE_MCP:
    mcp = FastMCP('qtgrace-mcp')

    _controller: Optional[QtGraceController] = None
    _batch: Optional[QtGraceBatchRenderer] = None


    def _get_controller(qtgrace_path: Optional[str] = None, workdir: Optional[str] = None) -> QtGraceController:
        global _controller
        if _controller is None or not _controller.is_running():
            _controller = QtGraceController(qtgrace_path or DEFAULT_QTGRACE, workdir)
        return _controller


    def _get_batch(qtgrace_path: Optional[str] = None) -> QtGraceBatchRenderer:
        global _batch
        if _batch is None:
            _batch = QtGraceBatchRenderer(qtgrace_path or DEFAULT_QTGRACE)
        return _batch


    @mcp.tool()
    def start_qtgrace(qtgrace_path: str = DEFAULT_QTGRACE, workdir: str = '',
                      device: str = 'PNG') -> str:
        """启动 qtgrace 进程并建立 TCP 控制通道。返回运行状态。

        device: 输出设备 PNG/EPS/PS/SVG/JPEG/BMP/TIFF (决定导出格式)。
        """
        ctrl = _get_controller(qtgrace_path, workdir or None)
        return ctrl.start(device)


    @mcp.tool()
    def plot_data(datasets: list, title: str = '', xlabel: str = '', ylabel: str = '',
                  legends: Optional[list] = None, style: str = '',
                  xmin: Optional[float] = None, xmax: Optional[float] = None,
                  ymin: Optional[float] = None, ymax: Optional[float] = None) -> str:
        """向 qtgrace 发送数据并绘图。

        datasets: [[[x1,x2,...],[y1,y2,...]], ...] 支持多条曲线。
        title/xlabel/ylabel: 可选标签。legends: 每条曲线的图例。
        style: 可选的额外 Grace 参数命令 (如 '@s0 line color 2')。
        xmin/xmax/ymin/ymax: 可选, 显式设置坐标范围。
        """
        parsed = []
        for ds in datasets:
            if len(ds) != 2:
                raise QtGraceError('每个数据集必须是 [x_list, y_list]')
            parsed.append((list(ds[0]), list(ds[1])))
        ctrl = _get_controller()
        return ctrl.plot(parsed, title, xlabel, ylabel, legends, style,
                         xmin, xmax, ymin, ymax)


    @mcp.tool()
    def export_image(filename: str = 'plot', format: str = 'png') -> str:
        """导出当前图为图像文件。返回输出文件绝对路径。

        filename: 输出文件名(不含扩展名或含)。绝对路径亦可。
        format: png / ps / eps / pdf / svg (取决于 qtgrace 设备支持)。
        """
        ctrl = _get_controller()
        return ctrl.export(filename, format)


    @mcp.tool()
    def plot_and_export(datasets: list, filename: str = 'plot', title: str = '', xlabel: str = '',
                        ylabel: str = '', legends: Optional[list] = None,
                        style: str = '', format: str = 'png',
                        use_batch: bool = False,
                        xmin: Optional[float] = None, xmax: Optional[float] = None,
                        ymin: Optional[float] = None, ymax: Optional[float] = None) -> str:
        """一步完成: 绘图 + 导出图像。返回输出文件绝对路径。

        use_batch=True 时使用 -batch 回退模式 (无需保持 GUI 进程, 但每次独立渲染)。
        xmin/xmax/ymin/ymax: 可选, 显式设置坐标范围。
        """
        if use_batch:
            renderer = _get_batch()
            return renderer.render(datasets, filename, title, xlabel, ylabel, legends, format)
        ctrl = _get_controller()
        # 若目标格式与当前设备不符, 自动重启 qtgrace 到对应设备
        dev_map = {'png': 'PNG', 'ps': 'PS', 'eps': 'EPS', 'svg': 'SVG',
                   'jpg': 'JPEG', 'jpeg': 'JPEG', 'bmp': 'BMP', 'tif': 'TIFF',
                   'tiff': 'TIFF', 'xpm': 'XPM', 'pdf': 'PDF'}
        target = dev_map.get((format or 'png').lower().lstrip('.'), 'PNG')
        if ctrl.device != target:
            ctrl.stop()
            ctrl.start(target)
        ctrl.plot(datasets, title, xlabel, ylabel, legends, style,
                  xmin, xmax, ymin, ymax)
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
    def save_agr(filename: str = 'plot.agr') -> str:
        """保存当前 qtgrace 项目为 .agr 参数文件。返回文件绝对路径。

        filename: 输出路径(绝对路径或相对名, 自动加 .agr)。
        """
        ctrl = _get_controller()
        if not filename.endswith('.agr'):
            filename += '.agr'
        return ctrl.save_agr(filename)


    @mcp.tool()
    def stop_qtgrace() -> str:
        """关闭 qtgrace 进程。"""
        global _controller
        if _controller is None:
            return 'qtgrace 未启动'
        return _controller.stop()


    @mcp.tool()
    def qtgrace_status() -> str:
        """查询 qtgrace 运行状态。"""
        global _controller
        if _controller is None or not _controller.is_running():
            return 'qtgrace 未运行'
        return (f'qtgrace 运行中 (pid={_controller.proc.pid}, '
                f'write_port={_controller.write_port}, read_port={_controller.read_port}, '
                f'workdir={_controller.workdir})')


    def main():
        mcp.run(transport='stdio')


if __name__ == '__main__':
    if not HAVE_MCP:
        print('需要安装 mcp SDK: pip install "mcp[cli]"', file=sys.stderr)
        sys.exit(1)
    main()
