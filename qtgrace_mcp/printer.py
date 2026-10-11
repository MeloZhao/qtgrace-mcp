"""Windows printer preflight and guard for QtGrace hardcopy.

Why this module exists
----------------------
QtGrace's ``-hardcopy`` path builds a ``QPrinter``, and on Windows that means
it asks Windows for the **default printer**.  If the default printer is a
network device that is currently unreachable (printer off, laptop away from the
office, VPN down), Windows throws up a modal *"正在等待打印机连接..."* /
*"Waiting for printer connection..."* dialog and the process blocks forever.

This has nothing to do with the desktop session.  It is easy to confirm:

    default printer = HP LaserJet (network, offline)  -> hangs
    default printer = Microsoft Print to PDF (local)  -> renders in <1 s

So we detect it and, optionally, temporarily point the default printer at a
local device for the duration of one render.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w  # noqa: E402  (only importable on Windows)

PRINTER_ATTRIBUTE_NETWORK = 0x00000010
PRINTER_ATTRIBUTE_LOCAL = 0x00000002

_winspool = None


def _sp():
    global _winspool
    if _winspool is None:
        _winspool = ctypes.WinDLL("winspool.drv")
    return _winspool


def available() -> bool:
    try:
        _sp()
        return True
    except (OSError, AttributeError):
        return False


class _PrinterInfo5(ctypes.Structure):
    """PRINTER_INFO_5: name + port, which is how we reliably spot network queues."""
    _fields_ = [("pPrinterName", w.LPWSTR),
                ("pPortName", w.LPWSTR),
                ("Attributes", w.DWORD),
                ("DeviceNotSelectedTimeout", w.DWORD),
                ("TransmissionRetryTimeout", w.DWORD)]


# Ports that need a reachable remote device.  Everything else (USB001, LPT1:,
# FILE:, PORTPROMPT:, nul:, XPSPort:, ...) is satisfied locally.
NETWORK_PORT_PREFIXES = ("ip_", "wsd", "\\\\", "tcp", "http", "lpd", "ipp")


def _port_is_network(port: str | None) -> bool:
    if not port:
        return False
    p = port.strip().lower()
    return any(p.startswith(pre) for pre in NETWORK_PORT_PREFIXES)


def list_printers() -> list:
    """Return [{'name', 'port', 'network'}, ...] for every installed printer."""
    if not available():
        return []
    sp = _sp()
    needed, returned = w.DWORD(0), w.DWORD(0)
    sp.EnumPrintersW(0x2 | 0x4, None, 5, None, 0,
                     ctypes.byref(needed), ctypes.byref(returned))
    if needed.value == 0:
        return []
    buf = ctypes.create_string_buffer(needed.value)
    if not sp.EnumPrintersW(0x2 | 0x4, None, 5, buf, needed.value,
                            ctypes.byref(needed), ctypes.byref(returned)):
        return []
    arr = ctypes.cast(buf, ctypes.POINTER(_PrinterInfo5))
    out = []
    for i in range(returned.value):
        port = arr[i].pPortName or ""
        out.append({
            "name": arr[i].pPrinterName,
            "port": port,
            # either flag says it, or the port itself is a remote one
            "network": bool(arr[i].Attributes & PRINTER_ATTRIBUTE_NETWORK)
                       or _port_is_network(port),
        })
    return out


def default_printer() -> str | None:
    if not available():
        return None
    buf = ctypes.create_unicode_buffer(512)
    size = ctypes.c_ulong(512)
    return buf.value if _sp().GetDefaultPrinterW(buf, ctypes.byref(size)) else None


def is_network_printer(name: str | None) -> bool:
    if not name:
        return False
    for p in list_printers():
        if p["name"] == name:
            return p["network"]
    return False


def local_candidates() -> list:
    """Printers that do not need the network, safest first."""
    prefs = ("microsoft print to pdf", "microsoft xps document writer")
    prs = list_printers()
    ordered = []
    for key in prefs:
        for p in prs:
            if key in p["name"].lower():
                ordered.append(p["name"])
    for p in prs:
        if not p["network"] and p["name"] not in ordered:
            ordered.append(p["name"])
    return ordered


def risky_default() -> bool:
    """True if the default printer is a remote queue that may block QtGrace."""
    cur = default_printer()
    return is_network_printer(cur)


def set_default_printer(name: str) -> bool:
    if not available() or not name:
        return False
    return bool(_sp().SetDefaultPrinterW(name))


def diagnose() -> dict:
    """Describe the current printer situation, for error messages."""
    cur = default_printer()
    info = {
        "default_printer": cur,
        "default_is_network": is_network_printer(cur),
        "printers": [f'{p["name"]}  [{p.get("port","")}]' for p in list_printers()],
        "local_candidates": local_candidates(),
    }
    if info["default_is_network"]:
        info["risky"] = True
        info["recommendation"] = (
            "把默认打印机换成本地设备（推荐 'Microsoft Print to PDF'），"
            "或者在出图期间让工具临时切换（--printer-guard）。"
            "网络打印机不可达时，QtGrace 的 -hardcopy 会弹出模态框"
            "「正在等待打印机连接…」并永久阻塞。")
    else:
        info["risky"] = False
    return info


class printer_guard:
    """Context manager: temporarily point the default printer at a local device.

    Only kicks in when the current default is a network printer.  The original
    is always restored, even on exceptions.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.original = None
        self.switched_to = None

    def __enter__(self):
        if not self.enabled or not available():
            return self
        cur = default_printer()
        if not cur or not is_network_printer(cur):
            return self
        for cand in local_candidates():
            if set_default_printer(cand):
                self.original = cur
                self.switched_to = cand
                break
        return self

    def __exit__(self, *exc):
        if self.original:
            set_default_printer(self.original)
        return False
