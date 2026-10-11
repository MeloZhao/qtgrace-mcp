#!/usr/bin/env python3
"""qtgrace-plot — command line front end for the QtGrace plotting core.

The MCP server is only *one* way to drive this code.  Everything it does is also
available here, without any protocol, client or restart:

    # render a figure from a JSON spec
    python qtgrace-plot spec.json -o fig.png

    # same, piped in
    cat spec.json | python qtgrace-plot - -o fig.png --device SVG

    # render an existing Grace project
    python qtgrace-plot --agr existing.agr -o fig.eps --device EPS

    # dump the capability catalogue (same data the MCP exposes)
    python qtgrace-plot --reference graph_types
    python qtgrace-plot --reference all --out-catalogue catalogue.json

    # print a starter spec you can edit
    python qtgrace-plot --example > spec.json

As a library:

    from qtgrace_mcp.agr import build_agr
    from qtgrace_mcp.batch import render

    render(agr_text=build_agr({"graphs": [...]}), device="PNG", output_path="fig.png")
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import catalog
from .agr import build_agr
from .batch import SUPPORTED_DEVICES, render, resolve_exe, resolve_home


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qtgrace-plot",
        description="Render publication figures with QtGrace, without the GUI and without MCP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("spec", nargs="?",
                   help="JSON spec file, or '-' to read from stdin")
    p.add_argument("-o", "--output", help="output file path")
    p.add_argument("--device", choices=list(SUPPORTED_DEVICES),
                   help="output device (default: inferred from the output extension, else PNG)")
    p.add_argument("--agr", metavar="FILE",
                   help="render an existing .agr project instead of a JSON spec")
    p.add_argument("--width-cm", type=float, help="override figure width (cm)")
    p.add_argument("--height-cm", type=float, help="override figure height (cm)")
    p.add_argument("--timeout-ms", type=int, default=120_000)
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--reference", nargs="?", const="overview", metavar="SECTION",
                   choices=catalog.sections(),
                   help="print a capability catalogue section and exit (default: overview)")
    p.add_argument("--out-catalogue", metavar="FILE",
                   help="with --reference, write the catalogue to a file instead of stdout")
    p.add_argument("--example", action="store_true",
                   help="print a starter JSON spec and exit")
    p.add_argument("--version", action="store_true", help="show the QtGrace build info")
    p.add_argument("--doctor", action="store_true",
                   help="check whether batch rendering actually works right now")
    p.add_argument("--printers", action="store_true",
                   help="show installed printers and which is the default")
    p.add_argument("--no-printer-guard", action="store_true",
                   help="do not temporarily switch the default printer during rendering")
    p.add_argument("--save-agr", nargs="?", const=True, default=False, metavar="FILE",
                   help="also write the .agr project (default: next to the output, same stem) "
                        "so it can be opened in the QtGrace GUI and tweaked by hand")
    p.add_argument("--no-bitmap-fallback", action="store_true",
                   help="do not fall back to PNM+convert when PNG/JPEG segfault")
    return p


EXAMPLE_SPEC = {
    "output_path": "D:/figs/sine.png",
    "device": "PNG",
    "width_cm": 8.6,
    "height_cm": 6.4,
    "graphs": [{
        "title": "Damped oscillation",
        "world": [0, -1.1, 6.2832, 1.1],
        "xaxis": {"label": "x (rad)", "tickMajor": 1.5708, "tickMinor": 0.7854, "tickPrec": 2},
        "yaxis": {"label": "y", "tickMajor": 0.5, "tickMinor": 0.25, "tickPrec": 2},
        "legend": {"pos": [0.62, 0.8]},
        "sets": [{
            "legend": "exp(-x/3) sin(x)",
            "lineType": 1, "lineColor": 2, "symbol": 0,
            "data": [[0, 0], [1, 0.2838], [2, 0.2579], [3, 0.0706], [4, 0.0], [5, -0.0435]],
        }],
    }],
}


def _load_spec(arg: str) -> dict:
    if arg == "-":
        return json.load(sys.stdin)
    with open(arg, encoding="utf-8") as fh:
        return json.load(fh)


def _infer_device(spec_device, output_path) -> str:
    if spec_device:
        return str(spec_device).upper()
    if output_path:
        ext = os.path.splitext(output_path)[1].lower().lstrip(".")
        mapping = {"png": "PNG", "svg": "SVG", "eps": "EPS", "ps": "PS",
                   "jpg": "JPEG", "jpeg": "JPEG", "pnm": "PNM", "mif": "MIF"}
        if ext in mapping:
            return mapping[ext]
    return "PNG"


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.printers:
        from .printer import diagnose, list_printers
        d = diagnose()
        print(f"默认打印机     : {d['default_printer']}")
        print(f"判为网络打印机 : {d['default_is_network']}"
              + ("   <== 不可达时会让 QtGrace 卡死" if d["risky"] else "   (本地，安全)"))
        print("已安装打印机:")
        for p in list_printers():
            tag = "[网络]" if p["network"] else "[本地]"
            print(f"  {tag} {p['name']:<44} port={p.get('port','')}")
        print(f"本地候选       : {d['local_candidates']}")
        if d.get("recommendation"):
            print(f"建议           : {d['recommendation']}")
        return 0

    if args.doctor:
        from .printer import diagnose
        from .batch import probe
        exe = resolve_exe()
        d = diagnose()
        print(f"executable   : {exe}")
        print(f"exists       : {os.path.isfile(exe)}")
        print(f"GRACE_HOME   : {resolve_home(exe)}")
        print(f"默认打印机   : {d['default_printer']}"
              + ("   <== 网络打印机，不可达时会卡死" if d["risky"] else "   (本地，OK)"))
        if d["risky"]:
            print(f"建议         : 换成 {d['local_candidates'][:2]} 之一，"
                  f"或保持 printer-guard（默认开启）自动临时切换")
        print("probing a minimal render ...")
        h = probe(force=True)
        print(f"rendering    : {'OK' if h['ok'] else 'FAIL'}")
        print(f"reason       : {h['reason']}")
        if h.get("detail"):
            print(f"detail       : {h['detail']}")
        if h.get("printer_temporarily_switched"):
            print(f"printer      : 已临时切到 {h['printer_temporarily_switched']}（渲染后还原）")
        if not h["ok"]:
            if h.get("fix"):
                print(f"fix          : {h['fix']}")
            if h.get("advice"):
                print(f"advice       : {h['advice']}")
        return 0 if h["ok"] else 1

    if args.version:
        exe = resolve_exe()
        print(f"qtgrace exe : {exe}")
        print(f"exists      : {os.path.isfile(exe)}")
        print(f"GRACE_HOME  : {resolve_home(exe)}")
        print(f"devices     : {', '.join(SUPPORTED_DEVICES)}")
        return 0

    if args.example:
        print(json.dumps(EXAMPLE_SPEC, indent=2, ensure_ascii=False))
        return 0

    if args.reference:
        data = catalog.get_section(args.reference)
        text = json.dumps(data, indent=2, ensure_ascii=False)
        if args.out_catalogue:
            with open(args.out_catalogue, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"catalogue written: {args.out_catalogue}")
        else:
            print(text)
        return 0

    if args.agr:
        if not os.path.isfile(args.agr):
            print(f"error: {args.agr} not found", file=sys.stderr)
            return 2
        if not args.output:
            print("error: -o/--output is required with --agr", file=sys.stderr)
            return 2
        result = render(agr_path=args.agr,
                        device=_infer_device(args.device, args.output),
                        output_path=args.output, timeout_ms=args.timeout_ms,
        printer_guard=not args.no_printer_guard,
        save_agr=args.save_agr,
        bitmap_fallback=not args.no_bitmap_fallback)
        return _report(result, args.json)

    if not args.spec:
        build_parser().print_help()
        return 1

    try:
        spec = _load_spec(args.spec)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read spec: {exc}", file=sys.stderr)
        return 2

    if args.width_cm:
        spec["width_cm"] = args.width_cm
    if args.height_cm:
        spec["height_cm"] = args.height_cm
    if args.output:
        spec["output_path"] = args.output
    if args.device:
        # an explicit --device must win over whatever the spec file says
        spec["device"] = args.device
    if not spec.get("output_path"):
        print("error: no output path (give -o or an 'output_path' in the spec)", file=sys.stderr)
        return 2

    try:
        agr = build_agr(spec)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("hint : run `qtgrace-plot --reference graph_types` for valid values",
              file=sys.stderr)
        return 3

    result = render(agr_text=agr,
                    device=_infer_device(spec.get("device"), spec["output_path"]),
                    output_path=spec["output_path"], timeout_ms=args.timeout_ms,
                    printer_guard=not args.no_printer_guard,
                    save_agr=args.save_agr if args.save_agr else spec.get("save_agr"),
                    bitmap_fallback=not args.no_bitmap_fallback)
    return _report(result, args.json)


def _report(result: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    if not result.get("ok"):
        print(f"error: {result.get('error')}", file=sys.stderr)
        return 1
    size = f"{result.get('width')}x{result.get('height')}" if result.get("width") else \
        f"{result.get('bytes')} bytes"
    print(f"{result['device']:5} {size:>12}  {result['output_path']}")
    if result.get("agr_path"):
        print(f"      {'':>12}  {result['agr_path']}   (Grace project, editable)")
    if result.get("bitmap_fallback"):
        print(f"      note: {result['bitmap_fallback']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
