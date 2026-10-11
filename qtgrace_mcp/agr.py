""".agr (Grace project) generator.

Verified constraints for QtGrace v0.2.6:

* Inline ``#`` comments are **not** accepted mid-line by this build's parser
  (only whole-line comments are).  This generator never emits inline comments.
* ``@version 50121`` (Grace 5.1.21/22) is accepted.
* Output file naming follows the *input* project name, so the caller controls it.
"""

from __future__ import annotations

CM_TO_PT = 72 / 2.54  # 1 inch = 72 pt = 2.54 cm

DEFAULT_FONTS = [
    ("Times-Roman", "Times-Roman"),
    ("Times-Italic", "Times-Italic"),
    ("Times-Bold", "Times-Bold"),
    ("Times-BoldItalic", "Times-BoldItalic"),
    ("Helvetica", "Helvetica"),
    ("Helvetica-Oblique", "Helvetica-Oblique"),
    ("Helvetica-Bold", "Helvetica-Bold"),
    ("Helvetica-BoldOblique", "Helvetica-BoldOblique"),
    ("Courier", "Courier"),
    ("Courier-Oblique", "Courier-Oblique"),
    ("Courier-Bold", "Courier-Bold"),
    ("Courier-BoldOblique", "Courier-BoldOblique"),
    ("Symbol", "Symbol"),
    ("ZapfDingbats", "ZapfDingbats"),
]

DEFAULT_COLORS = [
    (255, 255, 255, "white"),
    (0, 0, 0, "black"),
    (255, 0, 0, "red"),
    (0, 255, 0, "green"),
    (0, 0, 255, "blue"),
    (255, 255, 0, "yellow"),
    (188, 143, 143, "brown"),
    (220, 220, 220, "grey"),
    (148, 0, 211, "violet"),
    (0, 255, 255, "cyan"),
    (255, 0, 255, "magenta"),
    (255, 165, 0, "orange"),
]


# Set types whose geometry is a bar/box: they must not draw a connecting line,
# and their body is painted from the symbol-fill pen (see drawsetbars()).
BAR_SET_TYPES = {"bar", "bardy", "bardydy", "xyboxplot"}

# Expected number of columns per set type, so bad input fails with a useful
# message instead of a bare float() error deep inside the generator.
SET_TYPE_COLUMNS = {
    "xy": 2, "bar": 2,
    "xydx": 3, "xydy": 3, "bardy": 3, "xyhilo": 3, "xyband": 3,
    "xysize": 3, "xyr": 3, "xycolor": 3, "xyvmap": 3, "xyz": 3,
    "xydxdy": 4, "xydxdx": 4, "xydydy": 4, "bardydy": 4, "xycolpat": 4,
    "xydxdxdydy": 6, "xyboxplot": 6,
}


def cm_to_pt(cm: float) -> float:
    return cm * CM_TO_PT


# Graph types accepted by `@gN type ...` (source: QtGrace examples/*.agr)
GRAPH_TYPE_NAMES = {
    "xy": "XY", "chart": "Chart", "polar": "Polar",
    "polarv2": "PolarV2", "polar_v2": "PolarV2", "pie": "Pie",
}

# Axis scaling accepted by `@ xaxes scale ...` (source: examples/*.agr, pars.c)
SCALE_NAMES = {
    "normal": "Normal", "linear": "Normal", "lin": "Normal",
    "log": "Logarithmic", "logarithmic": "Logarithmic",
    "reciprocal": "Reciprocal", "logit": "Logit",
}


def _graph_type_name(value: str) -> str:
    key = str(value).strip().lower().replace(" ", "")
    if key not in GRAPH_TYPE_NAMES:
        raise ValueError(
            f"unknown graph_type '{value}'; use one of: "
            + ", ".join(sorted(set(GRAPH_TYPE_NAMES.values()))))
    return GRAPH_TYPE_NAMES[key]


def _scale_name(value: str) -> str:
    key = str(value).strip().lower()
    if key not in SCALE_NAMES:
        raise ValueError(
            f"unknown axis scale '{value}'; use one of: "
            + ", ".join(sorted(set(SCALE_NAMES.values()))))
    return SCALE_NAMES[key]


def _clean(line: str) -> str:
    """Strip what the parser would reject, while keeping whole-line comments.

    This QtGrace build rejects an inline ``#`` (the rest of the line becomes a
    syntax error), but:

    * a line that *starts* with ``#`` is a legitimate comment and is preserved
      (including the ``# Grace project file`` header), and
    * a ``#`` inside a quoted string is data, not a comment — otherwise a title
      such as ``"rate #1"`` would be silently truncated.
    """
    line = line.replace("\r", "")
    if line.lstrip().startswith("#"):
        return line.rstrip()

    out, in_quote = [], False
    for ch in line:
        if ch == '"':
            in_quote = not in_quote
        elif ch == "#" and not in_quote:
            break
        out.append(ch)
    return "".join(out).rstrip()


def _q(value) -> str:
    return '"' + str(value).replace('"', "") + '"'


def _num(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"data value {value!r} is not a number") from None
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"data value {value!r} is not finite")
    return f"{v:.10f}".rstrip("0").rstrip(".") or "0"


def _validate_rows(set_type: str, rows, where: str) -> None:
    """Fail early and clearly when the data does not match the set type."""
    want = SET_TYPE_COLUMNS.get(set_type)
    if want is None:
        raise ValueError(
            f"{where}: unknown set type '{set_type}'. Known types: "
            + ", ".join(sorted(SET_TYPE_COLUMNS)))
    if not rows:
        raise ValueError(
            f"{where}: set type '{set_type}' has no data rows; "
            f"supply rows of {want} numbers (see qtgrace_reference section='set_types')")
    for i, row in enumerate(rows):
        if not isinstance(row, (list, tuple)):
            raise ValueError(f"{where}: data row {i} must be a list of numbers, got {type(row).__name__}")
        n = len([v for v in row if v is not None])
        if n != want:
            raise ValueError(
                f"{where}: set type '{set_type}' needs {want} numbers per row "
                f"but row {i} has {n} ({list(row)!r})")


def build_agr(spec: dict) -> str:
    """Build a Grace project file from a declarative spec.

    Spec keys: width_cm, height_cm, page_size_pt, scale, graphs[]
    Graph keys: title, subtitle, world, view, xaxis, yaxis, legend, sets[]
    Axis keys: label, tickMajor, tickMinor, tickFormat, tickPrec, grid,
               labelSize, tickSize, tickOp
    Set keys: legend, type, lineType, lineStyle, lineColor, lineWidth,
              symbol, symbolSize, symbolColor, data[[x, y] | [x, y, yerr]]
    """
    lines: list[str] = []

    page_w, page_h = 792.0, 612.0
    if spec.get("page_size_pt"):
        page_w, page_h = spec["page_size_pt"]
    elif spec.get("width_cm") and spec.get("height_cm"):
        page_w = cm_to_pt(spec["width_cm"])
        page_h = cm_to_pt(spec["height_cm"])

    scale = float(spec.get("scale") or 1.0)
    if scale <= 0:
        scale = 1.0

    lines.append("# Grace project file")
    lines.append("@version 50121")

    # project description (shows up in File/Describe)
    if spec.get("description"):
        lines.append(f"@description {_q(spec['description'])}")

    # global defaults applied to anything not set explicitly
    dfl = spec.get("defaults") or {}
    if dfl:
        lines.append(f"@default linewidth {_num(dfl.get('linewidth', 1.0))}")
        lines.append(f"@default linestyle {dfl.get('linestyle', 1)}")
        lines.append(f"@default color {dfl.get('color', 1)}")
        lines.append(f"@default pattern {dfl.get('pattern', 1)}")
        lines.append(f"@default font {dfl.get('font', 0)}")
        lines.append(f"@default char size {_num(dfl.get('charSize', 1.0))}")
        lines.append(f"@default symbol size {_num(dfl.get('symbolSize', 1.0))}")

    # date handling for time-based axes
    date_cfg = spec.get("date")
    if date_cfg is not None:
        if not isinstance(date_cfg, dict):
            date_cfg = {}
        lines.append(f"@reference date {_num(date_cfg.get('reference', 0))}")
        lines.append(f"@date wrap {'on' if date_cfg.get('wrap') else 'off'}")
        lines.append(f"@date wrap year {_num(date_cfg.get('wrapYear', 1950))}")
    lines.append(f"@page size {_num(page_w * scale)}, {_num(page_h * scale)}")
    lines.append("@page scroll 5%")
    lines.append("@page inout 5%")
    lines.append("@link page off")

    for i, (a, b) in enumerate(spec.get("fonts") or DEFAULT_FONTS):
        lines.append(f"@map font {i} to {_q(a)}, {_q(b or a)}")

    for i, (r, g, b, name) in enumerate(spec.get("colors") or DEFAULT_COLORS):
        lines.append(f"@map color {i} to ({r}, {g}, {b}), {_q(name or 'c' + str(i))}")

    lines.append("@background color 0")
    lines.append("@default linewidth 1.0")
    lines.append("@default linestyle 1")
    lines.append("@default color 1")
    lines.append("@default pattern 1")
    lines.append("@default font 0")
    lines.append("@default char size 1.0")
    lines.append("@default symbol size 1.0")

    for gi, graph in enumerate(spec.get("graphs") or []):
        # graph type is a top-level statement: @g0 type XY|Chart|Polar|PolarV2|Pie
        gtype = graph.get("graph_type") or graph.get("graphType")
        if gtype:
            lines.append(f"@g{gi} type {_graph_type_name(gtype)}")
        is_chart = bool(gtype) and _graph_type_name(gtype) == "Chart"

        # every graph must be switched on explicitly, otherwise only g0 renders
        lines.append(f"@g{gi} on")
        lines.append(f"@g{gi} hidden false")

        # autoscale control: AUTO / NONE / XAXES / YAXES / XYAXES
        if graph.get("autoscale"):
            lines.append(f"@g{gi} autoscale type {str(graph['autoscale']).upper()}")

        # fixed-point number formatting for the graph's readout
        fp = graph.get("fixedpoint")
        if fp:
            if not isinstance(fp, dict):
                fp = {}
            xy = fp.get("xy", [0.0, 0.0])
            fmt = fp.get("format", ["general", "general"])
            if isinstance(fmt, str):
                fmt = [fmt, fmt]
            prec = fp.get("prec", [6, 6])
            if isinstance(prec, (int, float)):
                prec = [prec, prec]
            lines.append(f"@g{gi} fixedpoint on")
            lines.append(f"@g{gi} fixedpoint type {fp.get('type', 0)}")
            lines.append(f"@g{gi} fixedpoint xy {_num(xy[0])}, {_num(xy[1])}")
            lines.append(f"@g{gi} fixedpoint format {fmt[0]} {fmt[1]}")
            lines.append(f"@g{gi} fixedpoint prec {_num(prec[0])}, {_num(prec[1])}")
        if is_chart:
            # bar width is 0.01*symsize in viewport units (drawsetbars in plotone.cpp)
            lines.append(f"@g{gi} bar hgap {_num(graph.get('barGap', 0.01))}")
            if graph.get("stacked"):
                lines.append(f"@g{gi} stacked true")

        lines.append("")
        lines.append(f"@with g{gi}")

        world = graph.get("world") or [0, 0, 1, 1]
        lines.append("@    world " + ", ".join(_num(v) for v in world))

        view = graph.get("view") or [0.15, 0.15, 1.15, 0.85]
        lines.append("@    view " + ", ".join(_num(v) for v in view))

        # world stack: up to 20 saved world/tick settings, cycled with the
        # "Cycle world" button.  NOTE the order is (xmin, xmax, ymin, ymax),
        # which differs from `world` (xmin, ymin, xmax, ymax).
        for entry in graph.get("worldStack") or []:
            if isinstance(entry, dict):
                w = entry.get("world") or [0, 1, 0, 1]
                lines.append("@    stack world " + ", ".join(_num(v) for v in w))
                ticks = entry.get("ticks")
                if ticks:
                    lines.append("@    stack world " + ", ".join(_num(v) for v in w)
                                 + " tick " + ", ".join(_num(v) for v in ticks))
            else:
                lines.append("@    stack world " + ", ".join(_num(v) for v in entry))

        # graph frame (the border around the plot area)
        frame = graph.get("frame")
        if frame is not None:
            if not isinstance(frame, dict):
                frame = {"type": frame}
            lines.append(f"@    frame type {frame.get('type', 0)}")
            lines.append(f"@    frame linestyle {frame.get('linestyle', 1)}")
            lines.append(f"@    frame linewidth {_num(frame.get('linewidth', 1.0) * scale)}")
            lines.append(f"@    frame color {frame.get('color', 1)}")
            lines.append(f"@    frame pattern {frame.get('pattern', 1)}")
            lines.append(f"@    frame background color {frame.get('bgColor', 0)}")
            lines.append(f"@    frame background pattern {frame.get('bgPattern', 0)}")

        # z normalisation for colour-mapped sets (xycolor / xyvmap)
        if graph.get("znorm") is not None:
            lines.append(f"@    znorm {_num(graph['znorm'])}")

        # axis scaling uses the plural "xaxes"/"yaxes" form
        for axis, plural in (("xaxis", "xaxes"), ("yaxis", "yaxes")):
            ax_scale = (graph.get(axis) or {}).get("scale")
            if ax_scale:
                lines.append(f"@    {plural} scale {_scale_name(ax_scale)}")

        lines.append(f"@    frame linewidth {_num(graph.get('frameLinewidth', 1.0))}")
        lines.append("@    frame linestyle 1")
        lines.append(f"@    frame color {graph.get('frameColor', 1)}")
        lines.append("@    frame pattern 1")
        lines.append(f"@    frame background color {graph.get('frameBackground', 0)}")
        lines.append("@    frame background pattern 0")

        if graph.get("title"):
            lines.append(f"@    title {_q(graph['title'])}")
            lines.append(f"@    title font {graph.get('titleFont', 0)}")
            lines.append(f"@    title size {_num(graph.get('titleSize', 1.5) * scale)}")
            lines.append(f"@    title color {graph.get('titleColor', 1)}")

        if graph.get("subtitle"):
            lines.append(f"@    subtitle {_q(graph['subtitle'])}")
            lines.append(f"@    subtitle font {graph.get('subtitleFont', 0)}")
            lines.append(f"@    subtitle size {_num(graph.get('subtitleSize', 1.1) * scale)}")
            lines.append(f"@    subtitle color {graph.get('subtitleColor', 1)}")

        for axis in ("xaxis", "yaxis"):
            a = graph.get(axis) or {}
            if a.get("label"):
                lines.append(f"@    {axis} label {_q(a['label'])}")
            lines.append(f"@    {axis} label font {a.get('labelFont', 0)}")
            lines.append(f"@    {axis} label char size {_num(a.get('labelSize', 1.2) * scale)}")
            lines.append(f"@    {axis} label color {a.get('labelColor', 1)}")

            if a.get("alt"):
                alt_name = "altxaxis" if axis == "xaxis" else "altyaxis"
                lines.append(f"@    {alt_name} tick on")
                lines.append(f"@    {alt_name} tick major "
                             f"{_num(a.get('altTickMajor', a.get('tickMajor', 1.0)))}")
                lines.append(f"@    {alt_name} tick minor "
                             f"{_num(a.get('altTickMinor', a.get('tickMinor', 0.5)))}")
            if a.get("zero"):
                # `xaxis type zero true` is what actually draws the axis through
                # zero; the zeroxaxis block only configures its ticks.
                lines.append(f"@    {axis} type zero true")
                zero_name = "zeroxaxis" if axis == "xaxis" else "zeroyaxis"
                lines.append(f"@    {zero_name} tick on")
                lines.append(f"@    {zero_name} tick major "
                             f"{_num(a.get('zeroTickMajor', a.get('tickMajor', 1.0)))}")
                lines.append(f"@    {zero_name} tick minor "
                             f"{_num(a.get('zeroTickMinor', a.get('tickMinor', 0.5)))}")
                if a.get("zeroColor") is not None:
                    lines.append(f"@    {zero_name} tick color {a['zeroColor']}")

            if a.get("bar"):
                lines.append(f"@    {axis} bar on")
                lines.append(f"@    {axis} bar color {a.get('barColor', 1)}")
                lines.append(f"@    {axis} bar linestyle 1")
                lines.append(f"@    {axis} bar linewidth 1.0")

            lines.append(f"@    {axis} tick major {_num(a.get('tickMajor', 1))}")
            lines.append(f"@    {axis} tick minor {_num(a.get('tickMinor', 0.5))}")
            lines.append(f"@    {axis} ticklabel font {a.get('tickFont', 0)}")
            lines.append(f"@    {axis} ticklabel char size {_num(a.get('tickSize', 1.0) * scale)}")
            lines.append(f"@    {axis} ticklabel color {a.get('tickColor', 1)}")

            fmt = a.get("tickFormat", "decimal")
            lines.append(f"@    {axis} ticklabel format {fmt}")
            if fmt == "decimal":
                lines.append(f"@    {axis} ticklabel prec {a.get('tickPrec', 2)}")

            lines.append(f"@    {axis} tick major color {a.get('tickColor', 1)}")
            lines.append(f"@    {axis} tick major linewidth {_num(a.get('tickLinewidth', 1.0))}")
            lines.append(f"@    {axis} tick minor color {a.get('tickColor', 1)}")
            lines.append(f"@    {axis} tick major grid {'on' if a.get('grid') else 'off'}")
            lines.append(f"@    {axis} tick minor grid off")
            lines.append(f"@    {axis} tick op {a.get('tickOp', 'both')}")
            lines.append(f"@    {axis} ticklabel op {a.get('tickOp', 'both')}")

        legend = graph.get("legend", {})
        if legend is False:
            lines.append("@    legend off")
        else:
            pos = (legend or {}).get("pos") or [0.65, 0.85]
            lines.append("@    legend " + ", ".join(_num(v) for v in pos))
            lines.append(f"@    legend font {(legend or {}).get('font', 0)}")
            lines.append(f"@    legend char size {_num((legend or {}).get('size', 0.9) * scale)}")
            lines.append(f"@    legend color {(legend or {}).get('color', 1)}")
            box = (legend or {}).get("box") or {}
            lines.append(f"@    legend box color {box.get('color', (legend or {}).get('boxColor', 1))}")
            lines.append(f"@    legend box pattern {box.get('pattern', 1)}")
            lines.append(f"@    legend box linewidth "
                         f"{_num(box.get('linewidth', (legend or {}).get('boxLinewidth', 1.0)))}")
            lines.append(f"@    legend box linestyle {box.get('linestyle', 1)}")
            lines.append(f"@    legend box fill color {box.get('fillColor', 0)}")
            lines.append(f"@    legend box fill pattern {box.get('fillPattern', 1)}")
            lines.append(f"@    legend length {(legend or {}).get('length', 4)}")
            lines.append(f"@    legend vgap {(legend or {}).get('vgap', 2)}")

        sets = graph.get("sets") or []
        for si, s in enumerate(sets):
            p = f"@    s{si}"
            stype = str(s.get("type", "xy")).lower()
            is_bar = stype in BAR_SET_TYPES
            lines.append(f"{p} type {s.get('type', 'xy')}")
            lines.append(f"{p} symbol {s.get('symbol', 0)}")
            lines.append(f"{p} symbol size {_num(s.get('symbolSize', 0.6) * scale)}")
            lines.append(f"{p} symbol color {s.get('symbolColor', 1)}")
            # for bar-family sets the bar body is painted from the symbol fill
            fill_default = s.get("lineColor", 1) if is_bar else s.get("symbolColor", 1)
            lines.append(f"{p} symbol fill color {s.get('symbolFillColor', fill_default)}")
            lines.append(f"{p} symbol fill pattern {s.get('symbolFillPattern', 1)}")
            lines.append(f"{p} symbol linewidth {_num(s.get('symbolLinewidth', 1.0))}")
            # bar sets must not draw a connecting line, otherwise the bar tops join up
            default_line_type = 0 if is_bar else 1
            lines.append(f"{p} line type {s.get('lineType', default_line_type)}")
            lines.append(f"{p} line linestyle {s.get('lineStyle', 1)}")
            lines.append(f"{p} line linewidth {_num(s.get('lineWidth', 1.0) * scale)}")
            lines.append(f"{p} line color {s.get('lineColor', 1)}")
            lines.append(f"{p} errorbar size {_num(s.get('errorbarSize', 0.5) * scale)}")
            lines.append(f"{p} errorbar linewidth {_num(s.get('errorbarLinewidth', 1.0))}")
            lines.append(f"{p} errorbar color {s.get('errorbarColor', s.get('lineColor', 1))}")
            lines.append(f"{p} errorbar riser linewidth {_num(s.get('errorbarRiser', 1.0))}")
            lines.append(f"{p} errorbar type {s.get('errorbarType', 'both')}")
            lines.append(f"{p} errorbar length {_num(s.get('errorbarLength', 1.0))}")
            lines.append(f"{p} errorbar linestyle {s.get('errorbarLineStyle', 1)}")
            lines.append(f"{p} errorbar riser linestyle {s.get('errorbarRiserStyle', 1)}")
            lines.append(f"{p} symbol pattern {s.get('symbolPattern', 1)}")
            lines.append(f"{p} symbol char {s.get('symbolChar', 65)}")
            lines.append(f"{p} symbol char font {s.get('symbolCharFont', 0)}")
            lines.append(f"{p} symbol skip {s.get('symbolSkip', 0)}")
            lines.append(f"{p} skip {s.get('skip', 0)}")
            if s.get("comment"):
                lines.append(f"{p} comment {_q(s['comment'])}")
            if s.get("legend"):
                lines.append(f"{p} legend {_q(s['legend'])}")

            # filled area / band (see examples/fills.agr: "s0 fill type 2")
            fill = s.get("fill")
            if fill:
                if not isinstance(fill, dict):
                    fill = {"type": fill}
                lines.append(f"{p} fill type {fill.get('type', 1)}")
                lines.append(f"{p} fill rule {fill.get('rule', 0)}")
                lines.append(f"{p} fill color {fill.get('color', s.get('lineColor', 1))}")
                lines.append(f"{p} fill pattern {fill.get('pattern', 1)}")
            else:
                lines.append(f"{p} fill type 0")

            # baseline (where a filled set is anchored) and drop lines
            base = s.get("baseline")
            if base:
                if not isinstance(base, dict):
                    base = {"type": base}
                lines.append(f"{p} baseline type {base.get('type', 0)}")
                lines.append(f"{p} baseline {'on' if base.get('on', True) else 'off'}")
            else:
                lines.append(f"{p} baseline type 0")
                lines.append(f"{p} baseline off")
            lines.append(f"{p} dropline {'on' if s.get('dropline') else 'off'}")

            # value labels drawn next to each point/bar
            av = s.get("avalue")
            if av:
                if not isinstance(av, dict):
                    av = {}
                off = av.get("offset", [0.0, 0.0])
                lines.append(f"{p} avalue on")
                lines.append(f"{p} avalue type {av.get('type', 2)}")
                lines.append(f"{p} avalue char size {_num(av.get('size', 1.0) * scale)}")
                lines.append(f"{p} avalue font {av.get('font', 0)}")
                lines.append(f"{p} avalue color {av.get('color', s.get('lineColor', 1))}")
                lines.append(f"{p} avalue rot {_num(av.get('rot', 0))}")
                lines.append(f"{p} avalue format {av.get('format', 'general')}")
                lines.append(f"{p} avalue prec {av.get('prec', 3)}")
                lines.append(f"{p} avalue offset {_num(off[0])}, {_num(off[1])}")
            else:
                lines.append(f"{p} avalue off")

        for si, s in enumerate(sets):
            lines.append("")
            lines.append(f"@target g{gi}.s{si}")
            stype = str(s.get("type", "xy")).lower()
            lines.append(f"@type {s.get('type', 'xy')}")
            rows = s.get("data") or []
            _validate_rows(stype, rows, where=f"graphs[{gi}].sets[{si}]")
            for row in rows:
                if isinstance(row, (list, tuple)):
                    values = [v for v in row if v is not None]
                else:
                    values = [row.get("x"), row.get("y"), row.get("yerr")]
                    values = [v for v in values if v is not None]
                lines.append(" ".join(_num(v) for v in values))
            lines.append("&")

    _emit_timestamp(lines, spec.get("timestamp"), scale)
    _emit_annotations(lines, spec.get("annotations"), scale)

    return "\n".join(_clean(ln) for ln in lines) + "\n"


def _emit_timestamp(lines: list, ts, scale: float) -> None:
    """`@timestamp` — the date/time stamp drawn on the page."""
    if not ts:
        return
    if not isinstance(ts, dict):
        ts = {}
    pos = ts.get("pos", [0.03, 0.03])
    lines.append("")
    lines.append("@timestamp on")
    lines.append(f"@timestamp {_num(pos[0])}, {_num(pos[1])}")
    lines.append(f"@timestamp color {ts.get('color', 1)}")
    lines.append(f"@timestamp rot {_num(ts.get('rot', 0))}")
    lines.append(f"@timestamp font {ts.get('font', 0)}")
    lines.append(f"@timestamp char size {_num(ts.get('size', 1.0) * scale)}")
    lines.append(f"@timestamp def {_q(ts.get('format') or time.strftime('%a %b %d %H:%M:%S %Y'))}")


def _emit_annotations(lines: list, annotations, scale: float) -> None:
    """Free-floating text and line/arrow annotations.

    Syntax reverse-engineered from examples/arrows.agr and examples/axes.agr:

        @with string
        @    string on
        @    string loctype world
        @    string g0
        @    string 0.6625, 0.2
        @    string color 1
        @    string rot 0
        @    string font 0
        @    string just 2
        @    string char size 1.000000
        @    string def "L"

        @with line
        @    line on
        @    line loctype world
        @    line g0
        @    line 0.1, 0.475, 0.875, 0.475
        @    line linewidth 0.5
        @    line linestyle 6
        @    line color 1
        @    line arrow 0
        @    line arrow type 0
        @    line arrow length 2.000000
        @    line arrow layout 1.000000, 0.000000
    """
    for ann in annotations or []:
        kind = str(ann.get("type", "text")).lower()
        loctype = ann.get("loctype", "world")
        graph = ann.get("graph")

        if kind in ("text", "string", "label"):
            pos = ann.get("pos") or [ann.get("x", 0.5), ann.get("y", 0.5)]
            lines.append("")
            lines.append("@with string")
            lines.append("@    string on")
            lines.append(f"@    string loctype {loctype}")
            if graph is not None:
                lines.append(f"@    string g{int(graph)}")
            lines.append(f"@    string {_num(pos[0])}, {_num(pos[1])}")
            lines.append(f"@    string color {ann.get('color', 1)}")
            lines.append(f"@    string rot {_num(ann.get('rot', 0))}")
            lines.append(f"@    string font {ann.get('font', 0)}")
            lines.append(f"@    string just {ann.get('just', 0)}")
            lines.append(f"@    string char size {_num(ann.get('size', 1.0) * scale)}")
            lines.append(f"@    string def {_q(ann.get('text', ''))}")

        elif kind in ("line", "arrow"):
            p1 = ann.get("from") or [ann.get("x1", 0.0), ann.get("y1", 0.0)]
            p2 = ann.get("to") or [ann.get("x2", 1.0), ann.get("y2", 1.0)]
            is_arrow = kind == "arrow" or bool(ann.get("arrow"))
            layout = ann.get("arrowLayout", [1.0, 0.0])
            lines.append("")
            lines.append("@with line")
            lines.append("@    line on")
            lines.append(f"@    line loctype {loctype}")
            if graph is not None:
                lines.append(f"@    line g{int(graph)}")
            lines.append(f"@    line {_num(p1[0])}, {_num(p1[1])}, {_num(p2[0])}, {_num(p2[1])}")
            lines.append(f"@    line linewidth {_num(ann.get('linewidth', 1.0) * scale)}")
            lines.append(f"@    line linestyle {ann.get('linestyle', 1)}")
            lines.append(f"@    line color {ann.get('color', 1)}")
            lines.append(f"@    line arrow {1 if is_arrow else 0}")
            lines.append(f"@    line arrow type {ann.get('arrowType', 0)}")
            lines.append(f"@    line arrow length {_num(ann.get('arrowLength', 2.0))}")
            lines.append(f"@    line arrow layout {_num(layout[0])}, {_num(layout[1])}")
            # `@line def` (top level, unindented) COMMITS the annotation. Without
            # it the block is parsed but the line is never added to the drawing —
            # it silently does not appear. (Strings commit via `string def "..."`.)
            lines.append("@line def")

        elif kind in ("ellipse", "box", "rect", "rectangle"):
            # `@with ellipse` / `@with box`, both committed by a top-level def
            # (see examples/motif.agr and examples/bar.agr)
            is_box = kind != "ellipse"
            kw = "box" if is_box else "ellipse"
            p1 = ann.get("from") or [ann.get("x1", 0.2), ann.get("y1", 0.2)]
            p2 = ann.get("to") or [ann.get("x2", 0.8), ann.get("y2", 0.8)]
            lines.append("")
            lines.append(f"@with {kw}")
            lines.append(f"@    {kw} on")
            lines.append(f"@    {kw} loctype {loctype}")
            if graph is not None:
                lines.append(f"@    {kw} g{int(graph)}")
            lines.append(f"@    {kw} {_num(p1[0])}, {_num(p1[1])}, {_num(p2[0])}, {_num(p2[1])}")
            lines.append(f"@    {kw} linestyle {ann.get('linestyle', 1)}")
            lines.append(f"@    {kw} linewidth {_num(ann.get('linewidth', 1.0) * scale)}")
            lines.append(f"@    {kw} color {ann.get('color', 1)}")
            lines.append(f"@    {kw} fill color {ann.get('fillColor', 1)}")
            lines.append(f"@    {kw} fill pattern {ann.get('fillPattern', 0)}")
            lines.append(f"@{kw} def")

        else:
            raise ValueError(
                f"unknown annotation type '{kind}'; "
                f"use 'text', 'line', 'arrow', 'ellipse' or 'box'")
