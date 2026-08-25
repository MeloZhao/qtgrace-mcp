#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qtgrace-mcp 单元测试 (无需 qtgrace 进程, 纯逻辑验证)。"""
import os
import struct
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qtgrace_mcp.server import (
    PLOT_INFO, WRITE_DATAVEC, REDRAW, END_COMM,
    WRITE_DATAVEC_FINISHED, initComm, endComm, readDataComm,
    DEVICE_MAP, QtGraceError, QtGraceController, build_batch_agr,
    _default_qtgrace_path,
)


# ---------------- 协议常量 ----------------
class TestProtocolConstants:
    def test_command_values(self):
        assert PLOT_INFO == 1
        assert WRITE_DATAVEC == 2
        assert REDRAW == 4
        assert WRITE_DATAVEC_FINISHED == 12
        assert END_COMM == 99

    def test_comm_mode_values(self):
        assert initComm == 0
        assert endComm == 1
        assert readDataComm == 2


# ---------------- 设备映射 ----------------
class TestDeviceMap:
    def test_common_formats(self):
        assert DEVICE_MAP["png"] == "PNG"
        assert DEVICE_MAP["jpg"] == "JPEG"
        assert DEVICE_MAP["jpeg"] == "JPEG"
        assert DEVICE_MAP["eps"] == "EPS"
        assert DEVICE_MAP["svg"] == "SVG"
        assert DEVICE_MAP["pdf"] == "PDF"

    def test_all_values_uppercase(self):
        for v in DEVICE_MAP.values():
            assert v == v.upper()

    def test_alias_formats_map_to_same_device(self):
        # jpg/jpeg 是别名, tif/tiff 是别名, 应映射到同一设备
        assert DEVICE_MAP["jpg"] == DEVICE_MAP["jpeg"]
        assert DEVICE_MAP["tif"] == DEVICE_MAP["tiff"]


# ---------------- 数据流构造 ----------------
class TestDataStreamConstruction:
    def test_cmd_pack(self):
        result = QtGraceController._cmd(REDRAW)
        assert result == struct.pack("i", REDRAW)
        assert len(result) == 4

    def test_plot_info_encodes_utf8(self):
        s = '@title "测试"\n'
        result = QtGraceController._plot_info(s)
        # 结构: [PLOT_INFO][len][payload]
        cmd = struct.unpack("i", result[:4])[0]
        assert cmd == PLOT_INFO
        length = struct.unpack("i", result[4:8])[0]
        assert length == len(s.encode("utf-8"))
        payload = result[8:].decode("utf-8")
        assert payload == s

    def test_datavec_matching_lengths(self):
        xs = [1.0, 2.0, 3.0]
        ys = [4.0, 5.0, 6.0]
        result = QtGraceController._datavec(xs, ys)
        cmd = struct.unpack("i", result[:4])[0]
        assert cmd == WRITE_DATAVEC
        n = struct.unpack("i", result[4:8])[0]
        assert n == 3
        # 解析 x 数据
        x_data = struct.unpack("ddd", result[8:32])
        assert x_data == (1.0, 2.0, 3.0)
        # 解析 y 数据
        y_data = struct.unpack("ddd", result[32:56])
        assert y_data == (4.0, 5.0, 6.0)

    def test_datavec_mismatched_lengths_raises(self):
        with pytest.raises(QtGraceError, match="x/y 长度不一致"):
            QtGraceController._datavec([1, 2, 3], [1, 2])

    def test_ps_filename(self):
        name = "output.png"
        result = QtGraceController._ps_filename(name)
        cmd = struct.unpack("i", result[:4])[0]
        assert cmd == 5  # PS_FILENAME
        length = struct.unpack("i", result[4:8])[0]
        assert length == len(name.encode("utf-8"))


# ---------------- build_batch_agr (命令脚本) ----------------
class TestBuildBatchAgr:
    def test_basic_structure(self):
        agr = build_batch_agr([([1, 2], [3, 4])], title="Test",
                              xlabel="X", ylabel="Y", legends=["s0"],
                              data_files=["data.dat"])
        assert 'READ XY "data.dat"' in agr
        assert 'TITLE "Test"' in agr
        assert 'XAXIS LABEL "X"' in agr
        assert 'YAXIS LABEL "Y"' in agr
        assert 'S0 LEGEND "s0"' in agr
        assert "AUTOSCALE" in agr

    def test_multiple_datasets(self):
        agr = build_batch_agr([
            ([1, 2], [3, 4]),
            ([5, 6], [7, 8]),
        ], legends=["a", "b"], data_files=["a.dat", "b.dat"])
        assert 'READ XY "a.dat"' in agr
        assert 'READ XY "b.dat"' in agr
        assert 'S0 LEGEND "a"' in agr
        assert 'S1 LEGEND "b"' in agr

    def test_no_legends(self):
        agr = build_batch_agr([([1], [2])], data_files=["d.dat"])
        assert 'S0 LEGEND' not in agr  # 空图例不输出

    def test_ends_with_newline(self):
        agr = build_batch_agr([([1], [2])], data_files=["d.dat"])
        assert agr.endswith("\n")

    def test_set_type_xydy(self):
        agr = build_batch_agr([([1, 2], [3, 4], [0.1, 0.2])],
                              set_type="xydy", data_files=["d.dat"])
        assert 'READ XYDY "d.dat"' in agr

    def test_set_type_bar(self):
        agr = build_batch_agr([([1, 2], [3, 4])],
                              set_type="bar", data_files=["d.dat"])
        assert 'READ BAR "d.dat"' in agr
        assert "S0 FILL TYPE 1" in agr

    def test_world_limits(self):
        agr = build_batch_agr([([1], [2])],
                              xmin=0, xmax=10, ymin=-1, ymax=1,
                              data_files=["d.dat"])
        assert "WORLD XMIN 0" in agr
        assert "WORLD XMAX 10" in agr
        assert "WORLD YMIN -1" in agr
        assert "WORLD YMAX 1" in agr
        assert "AUTOSCALE" not in agr  # 全部指定后不自动缩放

    def test_requires_data_files(self):
        with pytest.raises(QtGraceError, match="data_files"):
            build_batch_agr([([1], [2])])

    def test_preset_style(self):
        agr = build_batch_agr([([1], [2])], style="journal",
                              data_files=["d.dat"])
        assert "frame linewidth 2" in agr  # @ 前缀被去掉, 保留小写
        assert "legend box off" in agr


# ---------------- 路径探测 ----------------
class TestPathDetection:
    def test_returns_string(self):
        path = _default_qtgrace_path()
        assert isinstance(path, str)
        assert len(path) > 0

    def test_env_variable_override(self, monkeypatch):
        monkeypatch.setenv("QTGRACE_PATH", "/custom/qtgrace")
        path = _default_qtgrace_path()
        assert path == "/custom/qtgrace"


# ---------------- QtGraceController 初始化 ----------------
class TestQtGraceControllerInit:
    def test_nonexistent_path_raises(self):
        with pytest.raises(QtGraceError, match="qtgrace 可执行文件不存在"):
            QtGraceController(qtgrace_path="/nonexistent/qtgrace.exe")

    def test_workdir_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = os.path.join(tmp, "subdir", "nested")
            # 使用一个存在的假文件路径来通过初始化检查
            fake_exe = os.path.join(tmp, "fake_qtgrace.exe")
            with open(fake_exe, "w") as f:
                f.write("fake")
            ctrl = QtGraceController(qtgrace_path=fake_exe, workdir=workdir)
            assert os.path.isdir(workdir)
            assert ctrl.device == "PNG"
            assert ctrl.is_running() is False


# ---------------- 错误处理 ----------------
class TestErrorHandling:
    def test_plot_empty_datasets_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_exe = os.path.join(tmp, "fake_qtgrace.exe")
            with open(fake_exe, "w") as f:
                f.write("fake")
            ctrl = QtGraceController(qtgrace_path=fake_exe, workdir=tmp)
            with pytest.raises(QtGraceError, match="没有数据"):
                ctrl.plot([])

    def test_export_wrong_device_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_exe = os.path.join(tmp, "fake_qtgrace.exe")
            with open(fake_exe, "w") as f:
                f.write("fake")
            ctrl = QtGraceController(qtgrace_path=fake_exe, workdir=tmp)
            ctrl.device = "PNG"
            with pytest.raises(QtGraceError, match="当前设备 PNG 不支持导出 pdf"):
                ctrl.export("test", format="pdf")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
