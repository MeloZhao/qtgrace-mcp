#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端功能测试: 实际启动 qtgrace 并绘图导出。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qtgrace_mcp.server import QtGraceController, QtGraceBatchRenderer


def test_tcp_plot_and_export():
    """TCP 模式: 启动 qtgrace -> 绘图 -> 导出 PNG -> 保存 .agr -> 关闭。"""
    print("=== TCP 模式测试 ===")
    outdir = os.path.join(tempfile.gettempdir(), "qtgrace_mcp_e2e")
    os.makedirs(outdir, exist_ok=True)

    ctrl = QtGraceController(workdir=outdir)
    print("[1] 启动 qtgrace (PNG)...")
    print("   ", ctrl.start("PNG"))

    print("[2] 绘制多曲线数据...")
    xs = [i * 0.1 for i in range(100)]
    y1 = [x ** 2 for x in xs]
    y2 = [x ** 1.5 * 10 for x in xs]
    result = ctrl.plot(
        [(xs, y1), (xs, y2)],
        title="E2E Test Plot",
        xlabel="Time (ns)",
        ylabel="RMSD (nm)",
        legends=["mutant", "wildtype"],
    )
    print("   ", result)

    print("[3] 导出 PNG...")
    png_path = ctrl.export(os.path.join(outdir, "e2e_plot"), format="png")
    print(f"    {png_path} ({os.path.getsize(png_path)} bytes)")

    print("[4] 保存 .agr...")
    agr_path = ctrl.save_agr(os.path.join(outdir, "e2e_plot.agr"))
    print(f"    {agr_path} ({os.path.getsize(agr_path)} bytes)")

    print("[5] 查询状态...")
    print(f"    running={ctrl.is_running()}, device={ctrl.device}")

    print("[6] 关闭 qtgrace...")
    print("   ", ctrl.stop())

    # 验证文件
    assert os.path.exists(png_path) and os.path.getsize(png_path) > 0, "PNG 未生成"
    assert os.path.exists(agr_path) and os.path.getsize(agr_path) > 0, ".agr 未生成"
    print("[OK] TCP 模式测试通过\n")
    return png_path, agr_path


def test_batch_renderer():
    """Batch 模式: 无需 TCP, 直接用 -batch 渲染。"""
    print("=== Batch 模式测试 ===")
    outdir = os.path.join(tempfile.gettempdir(), "qtgrace_mcp_e2e_batch")
    os.makedirs(outdir, exist_ok=True)

    renderer = QtGraceBatchRenderer(workdir=outdir)
    xs = [i * 0.5 for i in range(20)]
    ys = [x * x for x in xs]

    print("[1] Batch 渲染 PNG...")
    out = renderer.render(
        [(xs, ys)],
        filename=os.path.join(outdir, "batch_plot"),
        title="Batch Test",
        xlabel="X", ylabel="Y",
        legends=["data"],
        fmt="png",
    )
    print(f"    {out} ({os.path.getsize(out)} bytes)")

    assert os.path.exists(out) and os.path.getsize(out) > 0, "Batch PNG 未生成"
    print("[OK] Batch 模式测试通过\n")
    return out


def test_device_switch():
    """测试设备切换 (PNG -> EPS)。"""
    print("=== 设备切换测试 ===")
    outdir = os.path.join(tempfile.gettempdir(), "qtgrace_mcp_e2e_dev")
    os.makedirs(outdir, exist_ok=True)

    ctrl = QtGraceController(workdir=outdir)
    ctrl.start("PNG")
    print(f"[1] 初始设备: {ctrl.device}")

    ctrl.stop()
    ctrl.start("EPS")
    print(f"[2] 切换后设备: {ctrl.device}")

    xs = [i for i in range(10)]
    ys = [i * i for i in range(10)]
    ctrl.plot([(xs, ys)], title="EPS Test")
    eps_path = ctrl.export(os.path.join(outdir, "eps_plot"), format="eps")
    print(f"[3] EPS 导出: {eps_path} ({os.path.getsize(eps_path)} bytes)")

    ctrl.stop()
    assert os.path.exists(eps_path) and os.path.getsize(eps_path) > 0, "EPS 未生成"
    print("[OK] 设备切换测试通过\n")


if __name__ == "__main__":
    try:
        test_tcp_plot_and_export()
        test_batch_renderer()
        test_device_switch()
        print("=" * 50)
        print("[ALL OK] 全部端到端测试通过!")
    except Exception as e:
        print(f"\n[FAIL] 测试失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
