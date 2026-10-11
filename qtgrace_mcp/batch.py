"""QtGrace batch renderer.

Reverse-engineered invocation contract (verified against QtGrace v0.2.6, Win7 build):

    qtgrace.exe -nosafe -noask -hardcopy -hdevice <DEV> <project.agr>

Findings that shape this module:

* ``-hardcopy`` sets ``gracebat = TRUE`` inside the program, which makes it print
  and quit without ever creating an interactive window.  No ``gracebat.exe``
  copy is needed (the original Grace ships a separate binary; QtGrace does not).
* ``-printfile <name>`` is **ignored** by this build.  The real output path is
  ``<dir of project.agr>/<basename of project.agr>.<device extension>``
  (see ``replaceSuffix(print_file, dev.fext)`` in replacement_main.cpp).
  Callers therefore control the output name by controlling the *input* file name.
* The shipped ``templates/Default.agr`` uses inline ``#`` comments that this
  parser rejects, so the process prints many harmless ``syntax error:`` lines on
  stdout.  Success is judged by the produced file, never by stdout.
* ``-hdevice PDF`` hangs in this build and ``-hdevice HPNG`` produces nothing.
  Both are excluded from :data:`SUPPORTED_DEVICES`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import struct
from pathlib import Path

SUPPORTED_DEVICES = ("PNG", "SVG", "EPS", "PS", "JPEG", "PNM", "MIF")
DEVICE_EXT = {
    "PNG": "png",
    "SVG": "svg",
    "EPS": "eps",
    "PS": "ps",
    "JPEG": "jpg",
    "PNM": "pnm",
    "MIF": "mif",
}
DEVICE_LABEL = {
    "PNG": "raster PNG, pixel size == page size in points (72 dpi)",
    "SVG": "vector SVG",
    "EPS": "vector Encapsulated PostScript",
    "PS": "vector PostScript",
    "JPEG": "raster JPEG",
    "PNM": "raster portable anymap",
    "MIF": "FrameMaker MIF",
}

# Candidate locations, mirroring server.py's convention so both halves of the
# package resolve the same executable.
_CANDIDATES = [
    "D:/qtgrace_v026_Win7/bin/qtgrace.exe",
    "C:/qtgrace/bin/qtgrace.exe",
    "C:/Program Files/qtgrace/bin/qtgrace.exe",
    "/usr/local/bin/qtgrace",
    "/usr/bin/qtgrace",
]
DEFAULT_EXE = next((c for c in _CANDIDATES if os.path.exists(c)), _CANDIDATES[0])

# How long a caller should wait before giving up by default.  QtGrace either
# finishes in a second or two, or it is blocked on a modal dialog and will never
# finish — so a long timeout only wastes the caller's time.
DEFAULT_TIMEOUT_MS = 60_000
PROBE_TIMEOUT_MS = 20_000

_PROBE_CACHE: dict = {"at": 0.0, "result": None}
_PROBE_TTL_S = 60.0

# Minimal project that must always render if the environment is healthy.
_PROBE_AGR = """\
# Grace project file
@version 50121
@page size 792, 612
@with g0
@    world 0, 0, 1, 1
@    s0 type xy
@    s0 line type 1
@target g0.s0
@type xy
0 0
1 1
&
"""

PRINTER_HINT = (
    "The usual cause is the DEFAULT PRINTER, not the desktop session. QtGrace's -hardcopy "
    "builds a QPrinter, which means it asks Windows for the default printer. If that printer "
    "is a network device that is currently unreachable (printer switched off, laptop away "
    "from the office, VPN down), Windows shows a modal '正在等待打印机连接...' / "
    "'Waiting for printer connection...' dialog and the process blocks forever.\n"
    "Fix: make the default printer a LOCAL device ('Microsoft Print to PDF' or "
    "'Microsoft XPS Document Writer'), or let this tool switch it temporarily with "
    "printer_guard (CLI: --printer-guard, which is on by default).\n"
    "Measured on this machine: network printer default -> hangs; "
    "'Microsoft Print to PDF' default -> renders in ~0.6 s."
)


def printer_diagnosis() -> dict:
    """Printer facts to attach to failures, so the message is actionable."""
    try:
        from .printer import diagnose
        return diagnose()
    except Exception:  # noqa: BLE001
        return {}


def resolve_exe() -> str:
    """QTGRACE_EXE (this module) > QTGRACE_PATH (server.py) > auto-detected."""
    return (os.environ.get("QTGRACE_EXE")
            or os.environ.get("QTGRACE_PATH")
            or DEFAULT_EXE)


def resolve_home(exe: str | None = None) -> str:
    env = os.environ.get("QTGRACE_HOME")
    if env:
        return env
    exe = exe or resolve_exe()
    return str(Path(exe).parent.parent)


def png_size(path: str) -> dict | None:
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, h = struct.unpack(">II", head[16:24])
        return {"width": w, "height": h, "dpi": 72}
    except OSError:
        return None


def render(
    *,
    agr_text: str | None = None,
    agr_path: str | None = None,
    device: str = "PNG",
    output_path: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    printer_guard: bool = True,
    save_agr=None,
    bitmap_fallback: bool = True,
) -> dict:
    """Render a Grace project to ``output_path``.

    ``printer_guard`` (default on)
        Temporarily point the Windows default printer at a local device while
        rendering, because an unreachable network printer makes QtGrace block on
        a modal dialog ("正在等待打印机连接...").  Always restored.

    ``save_agr`` (default off)
        Also write the generated ``.agr`` project so it can be opened in the
        QtGrace GUI and fine-tuned by hand.  ``True`` puts it next to the output
        file with the same stem; a string is used as an explicit path.

    ``bitmap_fallback`` (default on)
        PNG and JPEG are the only devices that go through Qt's own raster paint
        engine, and QtGrace segfaults on some projects there.  When that
        happens, re-render with PNM (Grace's own driver, never crashes) and
        convert to the requested bitmap with Pillow.
    """
    exe = resolve_exe()
    if not os.path.isfile(exe):
        return {"ok": False, "error": f"QtGrace executable not found: {exe}"}

    dev = str(device or "PNG").upper()
    ext = DEVICE_EXT.get(dev)
    if not ext:
        return {
            "ok": False,
            "error": f'unsupported device "{device}"; supported: {", ".join(SUPPORTED_DEVICES)}',
        }

    if not agr_text and not agr_path:
        return {"ok": False, "error": "either agr_text or agr_path is required"}
    if agr_path and not os.path.isfile(agr_path):
        return {"ok": False, "error": f"agr file not found: {agr_path}"}

    tmp = tempfile.mkdtemp(prefix="qtgrace-")
    try:
        project = os.path.join(tmp, "chart.agr")
        if agr_path:
            shutil.copyfile(agr_path, project)
        else:
            with open(project, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(agr_text)

        env = dict(os.environ)
        env["GRACE_HOME"] = resolve_home(exe)

        from .printer import printer_guard as _Guard

        def attempt(device_name: str, device_ext: str):
            """One QtGrace run.  -> (proc, produced, timeout_result, switched)"""
            try:
                with _Guard(printer_guard) as guard:
                    proc = subprocess.run(
                        [exe, "-nosafe", "-noask", "-hardcopy", "-hdevice",
                         device_name, project],
                        cwd=os.path.dirname(exe), env=env,
                        capture_output=True, timeout=timeout_ms / 1000.0,
                    )
                return proc, os.path.join(tmp, f"chart.{device_ext}"), None, guard.switched_to
            except subprocess.TimeoutExpired:
                return None, None, _timeout_result(timeout_ms), None

        proc, produced, timeout_res, switched = attempt(dev, ext)
        if timeout_res:
            return timeout_res

        if not os.path.isfile(produced):
            crashed = _crashed(proc.returncode, proc.stderr)

            # ---- bitmap fallback: PNM then convert -------------------------
            if crashed and dev in ("PNG", "JPEG") and bitmap_fallback:
                _p2, prod2, _t2, _s2 = attempt("PNM", "pnm")
                if prod2 and os.path.isfile(prod2):
                    conv_ok, conv_err = _convert_bitmap(prod2, output_path, dev)
                    if conv_ok:
                        out = os.path.abspath(output_path)
                        info = {
                            "ok": True,
                            "output_path": out,
                            "device": dev,
                            "printer_temporarily_switched": switched,
                            "bytes": os.path.getsize(out),
                            "bitmap_fallback": (
                                f"QtGrace segfaults on this project with {dev}; rendered "
                                f"via PNM (Grace's own driver) and converted with Pillow"),
                        }
                        if save_agr:
                            info["agr_path"] = _persist_agr(project, save_agr, out)
                        if dev == "PNG":
                            size = png_size(out)
                            if size:
                                info.update(size)
                        return info
                    conv_err_note = conv_err
                else:
                    conv_err_note = "PNM fallback also produced nothing"

            result = {
                "ok": False,
                "error": ("QtGrace crashed while drawing this project"
                          if crashed else "QtGrace did not produce an output file"),
                "error_code": "crash" if crashed else "no_output",
                "device": dev,
                "returncode": proc.returncode,
                "stdout_tail": _tail(proc.stdout),
                "stderr_tail": _tail(proc.stderr),
            }
            if crashed and dev in ("PNG", "JPEG"):
                result["hint"] = (
                    "PNG and JPEG are the only devices that go through Qt's own raster "
                    "paint engine; certain projects (e.g. examples/pie.agr) segfault there "
                    "inside QtGrace itself. The same project renders fine with SVG, EPS, PS, "
                    "MIF or PNM.")
                result["suggested_devices"] = ["SVG", "EPS", "PS", "PNM"]
                if bitmap_fallback and conv_err_note:
                    result["bitmap_fallback_failed"] = conv_err_note
            elif crashed:
                result["hint"] = ("QtGrace segfaulted on this project. Try another device, "
                                  "or simplify the project.")
            return result

        out = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        shutil.copyfile(produced, out)

        info = {
            "ok": True,
            "output_path": out,
            "device": dev,
            "printer_temporarily_switched": switched,
            "bytes": os.path.getsize(out),
        }
        if save_agr:
            info["agr_path"] = _persist_agr(project, save_agr, out)
        if dev == "PNG":
            size = png_size(out)
            if size:
                info.update(size)
        return info
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _timeout_result(timeout_ms: int) -> dict:
    """Build the actionable result for a QtGrace run that had to be killed."""
    diag = printer_diagnosis()
    result = {
        "ok": False,
        "error_code": "timeout",
        "error": (f"QtGrace did not finish within {timeout_ms} ms and was killed. "
                  f"It is almost always blocked on a modal dialog — most often the "
                  f"printer one (see 'diagnosis')."),
        "diagnosis": PRINTER_HINT,
        "printer": diag,
    }
    if diag.get("risky"):
        result["hint"] = (f"默认打印机 '{diag['default_printer']}' 是网络打印机且可能不可达。"
                          f"换成 {diag['local_candidates'][:2]} 之一，"
                          f"或确认 printer_guard 已开启。")
    else:
        result["hint"] = ("Run the doctor (`qtgrace_plot.py --doctor`) to confirm. "
                          "If the default printer is fine, check that no other modal "
                          "dialog (e.g. the setup wizard) is waiting for input.")
    return result


def _persist_agr(project: str, save_agr, output_path: str) -> str:
    """Copy the generated project out of the temp dir so the user can open it."""
    if save_agr is True or save_agr == "" or save_agr is None:
        dest = os.path.splitext(output_path)[0] + ".agr"
    else:
        dest = str(save_agr)
        if not os.path.splitext(dest)[1]:
            dest += ".agr"
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    shutil.copyfile(project, dest)
    return os.path.abspath(dest)


def _read_pnm(path: str):
    """Parse a binary PPM (P6).  -> (width, height, raw_rgb_bytes)"""
    with open(path, "rb") as fh:
        data = fh.read()

    pos = 0

    def token():
        nonlocal pos
        while pos < len(data):
            c = data[pos:pos + 1]
            if c in b" \t\r\n":
                pos += 1
            elif c == b"#":                      # comment runs to end of line
                while pos < len(data) and data[pos:pos + 1] != b"\n":
                    pos += 1
            else:
                break
        start = pos
        while pos < len(data) and data[pos:pos + 1] not in b" \t\r\n":
            pos += 1
        return data[start:pos]

    magic = token()
    if magic != b"P6":
        raise ValueError(f"only binary PPM (P6) is supported, got {magic!r}")
    w, h, maxval = int(token()), int(token()), int(token())
    if maxval != 255:
        raise ValueError(f"only 8-bit PPM is supported, got maxval={maxval}")
    pos += 1                                     # single whitespace after maxval
    pixels = data[pos:pos + w * h * 3]
    if len(pixels) != w * h * 3:
        raise ValueError(f"truncated PPM: expected {w*h*3} bytes, got {len(pixels)}")
    return w, h, pixels


def _pnm_to_png(pnm_path: str, out_path: str) -> None:
    """Convert a binary PPM to PNG using only the standard library.

    QtGrace's PNM driver is one of its own (never segfaults), and PNG is just
    zlib-compressed scanlines, so this keeps the bitmap fallback dependency-free.
    """
    import struct
    import zlib

    w, h, pixels = _read_pnm(pnm_path)
    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)                            # filter type 0 (None)
        raw += pixels[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)   # 8-bit truecolour RGB
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    with open(out_path, "wb") as fh:
        fh.write(png)


def _convert_bitmap(pnm_path: str, output_path: str, dev: str):
    """PNM -> PNG (stdlib) or JPEG (Pillow).  -> (ok, error_message)"""
    out = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    try:
        if dev == "JPEG":
            try:
                from PIL import Image
            except ImportError:
                # no JPEG encoder in the stdlib; PNG is the honest fallback
                png_out = os.path.splitext(out)[0] + ".png"
                _pnm_to_png(pnm_path, png_out)
                return False, (f"JPEG needs Pillow, which is not installed; wrote "
                               f"{png_out} instead")
            w, h, pixels = _read_pnm(pnm_path)
            with Image.frombytes("RGB", (w, h), bytes(pixels)) as im:
                im.save(out, "JPEG", quality=95, dpi=(200, 200))
        else:
            _pnm_to_png(pnm_path, out)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"PNM conversion failed: {exc}"


def _crashed(returncode: int | None, stderr: bytes | None) -> bool:
    """True when QtGrace died on a signal rather than exiting normally."""
    if returncode is not None and returncode != 0:
        # Windows reports an unhandled exception as a large positive code
        if returncode >= 0xC0000000 or returncode == 3:
            return True
    if stderr and (b"SIGSEGV" in stderr or b"Got SIG" in stderr):
        return True
    return False


def _tail(raw: bytes | None, lines: int = 3) -> str:
    if not raw:
        return ""
    text = raw.decode("utf-8", "replace")
    kept = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def probe(timeout_ms: int = PROBE_TIMEOUT_MS, force: bool = False) -> dict:
    """Check whether batch rendering actually works right now.

    Renders a minimal project to a temp file.  Cached for a short while so an
    agent can call it freely.  Use this before a long batch run.
    """
    import time as _time
    now = _time.time()
    if not force and _PROBE_CACHE["result"] and now - _PROBE_CACHE["at"] < _PROBE_TTL_S:
        return dict(_PROBE_CACHE["result"])

    exe = resolve_exe()
    if not os.path.isfile(exe):
        result = {"ok": False, "reason": f"executable not found: {exe}",
                  "advice": "set QTGRACE_EXE to your qtgrace.exe"}
    else:
        tmp = tempfile.mkdtemp(prefix="qtgrace-probe-")
        try:
            out = os.path.join(tmp, "probe.png")
            r = render(agr_text=_PROBE_AGR, device="PNG", output_path=out,
                       timeout_ms=timeout_ms)
            if r.get("ok"):
                result = {"ok": True, "reason": "batch rendering works",
                          "detail": f"{r.get('width')}x{r.get('height')} test render produced",
                          "printer_temporarily_switched": r.get("printer_temporarily_switched")}
            elif r.get("error_code") == "timeout":
                diag = printer_diagnosis()
                reason = ("QtGrace blocked — default printer may be an unreachable "
                          "network printer" if diag.get("risky")
                          else "QtGrace blocked — a modal dialog is waiting for input")
                result = {"ok": False, "reason": reason, "advice": PRINTER_HINT,
                          "printer": diag}
                if diag.get("risky"):
                    result["fix"] = (f"把默认打印机换成 {diag['local_candidates'][:2]} 之一"
                                     f"（当前是 '{diag['default_printer']}'）")
            else:
                result = {"ok": False, "reason": r.get("error", "unknown failure"),
                          "advice": "check that the Qt5 DLLs are next to qtgrace.exe"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    _PROBE_CACHE["at"] = now
    _PROBE_CACHE["result"] = result
    return dict(result)
