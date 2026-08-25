#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""完整性审计: 格式导出(含自动切设备) + 边界情况。

使用 qtgrace_mcp.server 中的 QtGraceController / QtGraceError。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from qtgrace_mcp.server import QtGraceController, QtGraceError  # noqa: E402

OUT = os.path.join(os.getcwd(), "figures", "qtgrace")
os.makedirs(OUT, exist_ok=True)

ctrl = QtGraceController(workdir=os.path.join(os.environ.get("TEMP", "/tmp"), "qtgrace_mcp_audit"))
print(ctrl.start("PNG"))

xs = [i * 0.5 for i in range(50)]
ys = [i * i * 0.05 for i in range(50)]

print("\n=== 格式导出 (自动切设备) ===")
for fmt in ["png", "eps", "svg", "jpg", "bmp"]:
    try:
        target = {"png": "PNG", "eps": "EPS", "svg": "SVG",
                  "jpg": "JPEG", "bmp": "BMP"}[fmt]
        if ctrl.device != target:
            ctrl.stop()
            ctrl.start(target)
        ctrl.plot([(xs, ys)], title=f"Fmt {fmt}")
        out = ctrl.export(os.path.join(OUT, f"audit_{fmt}"), format=fmt)
        sz = os.path.getsize(out)
        with open(out, "rb") as f:
            head = f.read(12)
        print(f"  {fmt:4s}: OK {sz:8d}B  head={head!r}")
    except QtGraceError as e:
        print(f"  {fmt:4s}: FAIL {e}")
    except Exception as e:
        print(f"  {fmt:4s}: ERROR {type(e).__name__}: {e}")

print("\n=== 边界情况 ===")
# 大数据 10 万点
ctrl.stop()
ctrl.start("PNG")
big_x = [i * 0.01 for i in range(100000)]
big_y = [(i % 100) * 0.1 for i in range(100000)]
try:
    ctrl.plot([(big_x, big_y)], title="Big")
    out = ctrl.export(os.path.join(OUT, "audit_big"), format="png")
    print("[1] 10万点:", os.path.getsize(out), "bytes OK")
except Exception as e:
    print("[1] 10万点 FAIL:", type(e).__name__, e)

# 空数据
try:
    ctrl.plot([])
    print("[2] 空数据 FAIL (未报错)")
except QtGraceError:
    print("[2] 空数据 OK 报错")

# x/y 不匹配
try:
    ctrl.plot([([1, 2, 3], [1, 2])])
    print("[3] x/y不匹配 FAIL")
except QtGraceError:
    print("[3] x/y不匹配 OK 报错")

# 停止后调用
ctrl.stop()
try:
    ctrl.plot([([1, 2], [1, 2])])
    print("[4] 停止后调用 FAIL")
except QtGraceError:
    print("[4] 停止后调用 OK 报错")

# 格式不支持
ctrl.start("PNG")
ctrl.plot([([1, 2], [1, 2])])
try:
    ctrl.export(os.path.join(OUT, "audit_bad"), format="pdf")
    print("[5] pdf 导出 OK (设备PNG+PDF?)")
except QtGraceError as e:
    print("[5] pdf 导出报错:", str(e)[:60])
ctrl.stop()

print("\n=== 审计完成 ===")
