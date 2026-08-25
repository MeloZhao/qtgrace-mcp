#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复刻 qtgrace 官方示例: 渲染 examples 目录全部 .agr 并生成 HTML 画廊。

用法:
  python -m qtgrace_mcp.examples.build_gallery
  python qtgrace_mcp/examples/build_gallery.py [--examples-dir DIR] [--format png] [--outdir DIR]

依赖 qtgrace_mcp.server 中的 render_examples_dir / DEFAULT_EXAMPLES_DIR。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from qtgrace_mcp.server import render_examples_dir, DEFAULT_EXAMPLES_DIR  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="复刻 qtgrace 示例并生成画廊")
    ap.add_argument("--examples-dir", default=DEFAULT_EXAMPLES_DIR,
                    help="示例目录 (默认 %(default)s)")
    ap.add_argument("--format", default="png", help="输出格式 (默认 png)")
    ap.add_argument("--outdir", default=None,
                    help="输出目录 (默认 examples_dir/gallery)")
    ap.add_argument("--no-gallery", action="store_true",
                    help="不生成 HTML 画廊")
    args = ap.parse_args()

    report = render_examples_dir(
        examples_dir=args.examples_dir,
        fmt=args.format,
        outdir=args.outdir,
        build_gallery=not args.no_gallery,
    )
    print(f"示例目录 : {report['examples_dir']}")
    print(f"输出目录 : {report['outdir']}")
    print(f"格式     : {report['format']}")
    print(f"总计 {report['total']} 个 .agr · 成功 {len(report['rendered'])} · "
          f"失败 {len(report['failed'])}")
    for r in report["rendered"]:
        print(f"  [OK]   {r['name']}  ({r.get('size', 0)} bytes)")
    for r in report["failed"]:
        print(f"  [FAIL] {r['name']}  {r['error']}")
    if report["gallery"]:
        print(f"\n画廊: {report['gallery']}")


if __name__ == "__main__":
    main()
