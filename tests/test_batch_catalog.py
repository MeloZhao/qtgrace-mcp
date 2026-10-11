"""Tests for the capability catalogue and the renderer contract."""

import os

import pytest

from qtgrace_mcp import catalog
from qtgrace_mcp.batch import DEVICE_EXT, SUPPORTED_DEVICES, probe, render


# --- catalogue ------------------------------------------------------------

def test_all_sections_present():
    for name in ["overview", "graph_types", "set_types", "symbols", "line_types",
                 "axes", "colors", "fonts", "expressions", "devices",
                 "recipes", "limitations"]:
        data = catalog.get_section(name)
        assert name in data, f"section {name} missing"
        assert data[name], f"section {name} is empty"


def test_unknown_section_reports_available():
    data = catalog.get_section("nope")
    assert "error" in data
    assert "available" in data


def test_five_graph_types():
    names = [g["name"] for g in catalog.GRAPH_TYPES]
    assert names == ["XY", "CHART", "POLAR", "POLARV2", "PIE"]


def test_nineteen_set_types():
    assert len(catalog.SET_TYPES) == 19


def test_every_set_type_has_columns():
    for s in catalog.SET_TYPES:
        assert s["cols"], f"{s['name']} has no column spec"
        assert s["desc"]


def test_recipes_are_valid_specs():
    """Every documented recipe must actually build."""
    from qtgrace_mcp.agr import build_agr
    for r in catalog.RECIPES:
        args = r["call"]["arguments"]
        spec = {"graphs": args["graphs"]}
        if "width_cm" in args:
            spec["width_cm"] = args["width_cm"]
        if "height_cm" in args:
            spec["height_cm"] = args["height_cm"]
        build_agr(spec)  # must not raise


def test_disabled_devices_are_marked():
    disabled = {d["name"] for d in catalog.DEVICES if not d["works"]}
    assert "PDF" in disabled and "HPNG" in disabled


def test_limitations_mention_the_printer_trap():
    """The #1 hang cause must be documented, because the symptom is a silent hang."""
    joined = " ".join(catalog.LIMITATIONS)
    assert "打印机" in joined and "printer" in joined.lower()


def test_printer_module_detects_network_queues():
    from qtgrace_mcp import printer
    if not printer.available():
        pytest.skip("not Windows / winspool unavailable")
    for p in printer.list_printers():
        # every printer must report a port, and network detection must be by port
        assert isinstance(p["network"], bool)
        assert "port" in p
    cur = printer.default_printer()
    if cur:
        assert isinstance(printer.is_network_printer(cur), bool)


def test_printer_guard_restores_the_original():
    """The guard must never leave the user's default printer changed."""
    from qtgrace_mcp import printer
    if not printer.available():
        pytest.skip("not Windows")
    original = printer.default_printer()
    if not original or not printer.is_network_printer(original):
        pytest.skip("default printer is already local — nothing to guard")
    with printer.printer_guard(True) as g:
        assert g.switched_to, "guard should have switched to a local printer"
    assert printer.default_printer() == original, "guard failed to restore"


# --- renderer contract ----------------------------------------------------

def test_supported_devices_match_extension_map():
    assert set(SUPPORTED_DEVICES) == set(DEVICE_EXT)


def test_unknown_device_rejected_without_spawning():
    r = render(agr_text="x", device="BOGUS", output_path="nope.png")
    assert r["ok"] is False
    assert "unsupported device" in r["error"]


def test_missing_executable_reports_clearly(monkeypatch):
    monkeypatch.setenv("QTGRACE_EXE", "D:/definitely/not/here/qtgrace.exe")
    r = render(agr_text="x", device="PNG", output_path="nope.png")
    assert r["ok"] is False
    assert "not found" in r["error"]


def test_render_requires_input():
    r = render(device="PNG", output_path="nope.png")
    assert r["ok"] is False
    assert "required" in r["error"]


def test_missing_agr_file_reports_clearly():
    r = render(agr_path="D:/definitely/not/here.agr", device="PNG", output_path="x.png")
    assert r["ok"] is False
    assert "not found" in r["error"]


def test_probe_returns_a_verdict():
    h = probe(timeout_ms=8000, force=True)
    assert "ok" in h and "reason" in h
    assert isinstance(h["ok"], bool)


# --- known QtGrace crash surface -----------------------------------------

def test_png_is_documented_as_crash_prone():
    """PNG/JPEG go through Qt's raster engine and can segfault; must be documented."""
    joined = " ".join(catalog.LIMITATIONS)
    assert "SIGSEGV" in joined or "段错误" in joined
    assert "PNG" in joined and "SVG" in joined


def test_crash_detection_helper():
    from qtgrace_mcp.batch import _crashed
    assert _crashed(3, b"Oops! Got SIGSEGV") is True
    assert _crashed(0xC0000005, None) is True
    assert _crashed(0, b"") is False
    assert _crashed(None, b"normal output") is False


def test_vector_devices_are_the_safe_set():
    """The devices Grace drives itself must all be available."""
    for d in ("SVG", "EPS", "PS", "PNM", "MIF"):
        assert d in SUPPORTED_DEVICES


# --- deliverables: .agr persistence and reliable bitmaps -----------------

def test_pnm_to_png_is_pure_stdlib(tmp_path):
    """The bitmap fallback must work without Pillow installed."""
    from qtgrace_mcp.batch import _pnm_to_png
    import struct

    ppm = tmp_path / "t.ppm"
    ppm.write_bytes(b"P6\n4 2\n255\n" + bytes([255, 0, 0] * 4 + [0, 0, 255] * 4))
    out = tmp_path / "t.png"
    _pnm_to_png(str(ppm), str(out))

    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    assert struct.unpack(">II", data[16:24]) == (4, 2)


def test_pnm_reader_rejects_non_p6(tmp_path):
    from qtgrace_mcp.batch import _read_pnm
    p = tmp_path / "a.pgm"
    p.write_bytes(b"P5\n4 2\n255\n" + bytes(8))
    with pytest.raises(ValueError):
        _read_pnm(str(p))


def test_pnm_reader_rejects_truncated(tmp_path):
    from qtgrace_mcp.batch import _read_pnm
    p = tmp_path / "t.ppm"
    p.write_bytes(b"P6\n4 2\n255\n" + bytes(3))
    with pytest.raises(ValueError):
        _read_pnm(str(p))


def test_persist_agr_uses_output_stem(tmp_path):
    from qtgrace_mcp.batch import _persist_agr
    proj = tmp_path / "chart.agr"
    proj.write_text("# Grace project file\n@version 50121\n", encoding="utf-8")
    out = str(tmp_path / "figure.png")
    got = _persist_agr(str(proj), True, out)
    assert got == str(tmp_path / "figure.agr")
    assert os.path.isfile(got)


def test_persist_agr_explicit_path(tmp_path):
    from qtgrace_mcp.batch import _persist_agr
    proj = tmp_path / "chart.agr"
    proj.write_text("# x\n", encoding="utf-8")
    got = _persist_agr(str(proj), str(tmp_path / "sub" / "custom"), str(tmp_path / "f.png"))
    assert got.endswith("custom.agr")
    assert os.path.isfile(got)


def test_render_signature_exposes_the_new_options():
    import inspect
    from qtgrace_mcp.batch import render
    params = inspect.signature(render).parameters
    assert "save_agr" in params
    assert "bitmap_fallback" in params
    assert params["bitmap_fallback"].default is True
