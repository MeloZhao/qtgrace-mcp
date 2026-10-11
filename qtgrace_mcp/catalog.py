"""Capability catalogue for the QtGrace MCP server.

Everything here was extracted from the QtGrace v0.2.6 source tree that ships
with the binary, so the codes and value ranges are the real ones rather than
guesses.  Source of each list is noted so a reader can verify it.

The point of this module is that an agent can read one tool result and
immediately know *what kinds of figures are possible* and *which numbers to
pass* — without trial and error.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Overview / workflow
# --------------------------------------------------------------------------

OVERVIEW = """\
QtGrace 是一个 2D 科研绘图程序（Grace/xmgrace 的 Qt5 移植）。这个 MCP 让它可以
**无 GUI 批量出图**：给定数据和样式，产出一张确定性、可复现的图。

## 最短工作流（三步）

1. 先读 `qtgrace_reference(section="graph_types")` 与 `section="set_types"`，
   确认你要画的图属于哪一类。
2. 调用 `make_plot`，把数据 + 轴 + 样式一次给全，直接得到图片文件。
3. 需要像素级微调时，再 `render_agr` 渲染手工写好的 `.agr`。

## 你要画什么 → 用哪个 graph_type

| 需求 | graph_type | set_type | 备注 |
|---|---|---|---|
| 折线 / 散点 / 曲线 | `XY`（默认） | `xy` | 最常用 |
| 带误差棒的曲线 | `XY` | `xydy`（对称）或 `xydxdy`（xy 双向） | 数据行 `[x, y, err]` |
| 阶梯图 | `XY` | `xy` + `lineType=2/3` | 2=左阶梯 3=右阶梯 |
| 柱状图 / 直方图 | `CHART` | `bar` / `bardy` | 需配 `xaxis.bar=true` |
| 箱线图 | `CHART` | `xyboxplot` | |
| 高低/范围图 | `XY` | `xyhilo` | |
| 填充区域（带子） | `XY` | `xyband` | 配 `setfill` |
| 气泡图（第三维映射大小） | `XY` | `xysize` | 数据行 `[x, y, size]` |
| 颜色映射散点 | `XY` | `xycolor` | 数据行 `[x, y, colorIndex]` |
| 极坐标图 | `POLAR` | `xy`（x 当角度） | 经典极坐标 |
| 增强极坐标（可设角度起点/中空半径） | `POLARV2` | `xy` | **QtGrace 扩展**，原版 Grace 不兼容 |
| 饼图 | `PIE` | `xy`（y 为占比） | |

> `graph_type` 直接写进 `.agr` 的 `@g0 type <TYPE>`，例如 `@g0 type Chart`。

### 柱状图要点（容易踩坑）

源码里 `drawsetbars()` 的柱宽公式是 **`bw = 0.01 × symbolSize`**（视口比例）：

- **柱宽由 `symbolSize` 决定**，不是由 `world` 的 x 间距决定。
  `symbolSize=0.6`（折线默认值）画出来的柱子只有视口宽的 0.6%，细如发丝；
  柱状图请显式给 `symbolSize: 3`（≈3% 视口宽）或更大。
- 柱子**填充**来自 `symbolFillColor` / `symbolFillPattern`；
  **描边**来自 `symbolColor` + `symbolLinewidth`。`lineColor` 管的是连接线。
- bar 类数据集（bar / bardy / bardydy / xyboxplot）**必须 `lineType=0`**，
  否则柱顶会被一条折线连起来。本 MCP 已自动处理。
- 需要 x 轴也画柱（分类轴）时设 `xaxis.bar=true`。

## 坐标轴

| 需求 | 做法 |
|---|---|
| 线性轴 | 默认 |
| 对数轴 | `xaxis.scale="log"` / `yaxis.scale="log"` |
| 双向对数 | 两个都设 |
| 精确轴范围 | `world=[xmin, ymin, xmax, ymax]` —— **强烈建议显式给**，否则每次自动缩放结果可能不同 |
| 主/次刻度间距 | `xaxis.tickMajor` / `xaxis.tickMinor` |
| 刻度数字精度 | `xaxis.tickPrec`（小数位） |
| 刻度格式 | `xaxis.tickFormat`（见 section="axes"） |
| 网格线 | `xaxis.grid=true` / `yaxis.grid=true` |
| 刻度朝向 | `xaxis.tickOp` = `both` / `in` / `out` / `off` |

## 样式

| 需求 | 做法 |
|---|---|
| 线型 | `sets[].lineType`（见 section="line_types"） |
| 线宽 | `sets[].lineWidth` |
| 符号 | `sets[].symbol`（见 section="symbols"） |
| 颜色 | `sets[].lineColor` / `symbolColor`，用调色板序号（见 section="colors"） |
| 多面板 | `graphs` 给多组，每组一个 `view` 视口 |
| 标题/副标题 | 顶层 `title` / `subtitle`，或每个 graph 单独给 |

## 输出

`device` 可选：`PNG` `SVG` `EPS` `PS` `JPEG` `PNM` `MIF`。
**矢量（SVG/EPS/PS）用于投稿**；PNG 在本构建里固定 792×612 像素。
"""

# --------------------------------------------------------------------------
# Graph types  (source: defines.h GRAPH_* / examples/*.agr)
# --------------------------------------------------------------------------

GRAPH_TYPES = [
    {"name": "XY", "agr": "@g0 type XY", "default": True,
     "desc": "普通直角坐标图。折线、散点、误差棒、填充、阶梯图都属于这一类。"},
    {"name": "CHART", "agr": "@g0 type Chart",
     "desc": "图表类：柱状图、箱线图、堆叠图。需要配合 xaxis bar 设置与 bar/bardy 类数据集。"},
    {"name": "POLAR", "agr": "@g0 type Polar",
     "desc": "经典极坐标图。数据集的 x 视为角度、y 视为半径。"},
    {"name": "POLARV2", "agr": "@g0 type PolarV2",
     "desc": "增强极坐标（QtGrace 扩展）：可设角度 0 的位置、半径下限、中空半径。"
             "输出文件与原版 Grace 不兼容。"},
    {"name": "PIE", "agr": "@g0 type Pie", "desc": "饼图。数据集的 y 值作为各扇区占比。"},
]

# --------------------------------------------------------------------------
# Set types  (source: replacement_main.cpp:1204 `-settype` usage string)
# --------------------------------------------------------------------------

SET_TYPES = [
    {"name": "xy", "cols": ["x", "y"], "desc": "标准 x-y 数据。最常用。"},
    {"name": "xydx", "cols": ["x", "y", "dx"], "desc": "x 方向误差棒。"},
    {"name": "xydy", "cols": ["x", "y", "dy"], "desc": "y 方向误差棒（最常用的一种）。"},
    {"name": "xydxdy", "cols": ["x", "y", "dx", "dy"], "desc": "x、y 双向误差棒。"},
    {"name": "xydxdx", "cols": ["x", "y", "dx1", "dx2"], "desc": "x 方向非对称误差棒。"},
    {"name": "xydydy", "cols": ["x", "y", "dy1", "dy2"], "desc": "y 方向非对称误差棒。"},
    {"name": "xydxdxdydy", "cols": ["x", "y", "dx1", "dx2", "dy1", "dy2"],
     "desc": "x、y 双向非对称误差棒。"},
    {"name": "xyz", "cols": ["x", "y", "z"], "desc": "带 z 值（可用于颜色/大小映射）。"},
    {"name": "bar", "cols": ["x", "y"], "desc": "柱状图（需 graph_type=CHART）。"},
    {"name": "bardy", "cols": ["x", "y", "dy"], "desc": "带误差棒的柱状图。"},
    {"name": "bardydy", "cols": ["x", "y", "dy1", "dy2"], "desc": "带非对称误差棒的柱状图。"},
    {"name": "xyhilo", "cols": ["x", "y_low", "y_high"], "desc": "高低线 / 范围图。"},
    {"name": "xyboxplot", "cols": ["x", "median", "q1", "q3", "whisker_lo", "whisker_hi"],
     "desc": "箱线图（需 graph_type=CHART）。"},
    {"name": "xyband", "cols": ["x", "y_low", "y_high"], "desc": "填充带（配 setfill 使用）。"},
    {"name": "xysize", "cols": ["x", "y", "size"], "desc": "气泡图：第三列控制符号大小。"},
    {"name": "xyr", "cols": ["x", "y", "r"], "desc": "极坐标半径显式给出的数据集。"},
    {"name": "xycolor", "cols": ["x", "y", "colorIndex"], "desc": "每个点单独取色。"},
    {"name": "xycolpat", "cols": ["x", "y", "colorIndex", "pattern"], "desc": "点级颜色 + 图案。"},
    {"name": "xyvmap", "cols": ["x", "y", "value"], "desc": "用数值映射到颜色表（value map）。"},
]

# --------------------------------------------------------------------------
# Symbols  (source: defines.h SYM_* and plotone.cpp switch)
# --------------------------------------------------------------------------

SYMBOLS = [
    {"code": 0, "name": "none", "desc": "不画符号（纯折线时用这个）"},
    {"code": 1, "name": "circle", "desc": "实心圆（最常用）"},
    {"code": 2, "name": "square", "desc": "方块"},
    {"code": 3, "name": "diamond", "desc": "菱形"},
    {"code": 4, "name": "triangle up", "desc": "上三角"},
    {"code": 5, "name": "triangle down", "desc": "下三角"},
    {"code": 6, "name": "triangle left", "desc": "左三角"},
    {"code": 7, "name": "triangle right", "desc": "右三角"},
    {"code": 8, "name": "plus", "desc": "加号"},
    {"code": 9, "name": "cross", "desc": "叉号"},
    {"code": 10, "name": "star", "desc": "星形（splat）"},
    {"code": 11, "name": "char", "desc": "用字符当符号"},
]

# --------------------------------------------------------------------------
# Line types  (source: defines.h LINE_TYPE_*)
# --------------------------------------------------------------------------

LINE_TYPES = [
    {"code": 0, "name": "none", "desc": "不画线（纯散点图）"},
    {"code": 1, "name": "straight", "desc": "折线连接数据点（最常用）"},
    {"code": 2, "name": "left stair", "desc": "左阶梯（阶跃图）"},
    {"code": 3, "name": "right stair", "desc": "右阶梯"},
    {"code": 4, "name": "segment2", "desc": "分段线型 2"},
    {"code": 5, "name": "segment3", "desc": "分段线型 3"},
    {"code": 6, "name": "increasing x", "desc": "递增 x"},
    {"code": 7, "name": "decreasing x", "desc": "递减 x"},
]

# --------------------------------------------------------------------------
# Tick label formats  (source: pars.c / y.tab.c keyword tables)
# --------------------------------------------------------------------------

TICK_FORMATS = [
    {"name": "decimal", "desc": "普通小数，用 tickPrec 控制小数位。默认。"},
    {"name": "exponential", "desc": "指数记法，如 1.23e+04"},
    {"name": "power", "desc": "幂记法"},
    {"name": "scientific", "desc": "科学记数法"},
    {"name": "engineering", "desc": "工程记数法（10 的 3 的倍数次幂）"},
    {"name": "mmddyy", "desc": "日期：月/日/年"},
    {"name": "mmddyyyy", "desc": "日期：月/日/四年"},
    {"name": "ddmmyy", "desc": "日期：日/月/年"},
    {"name": "yymmdd", "desc": "日期：年/月/日"},
    {"name": "mmdd", "desc": "日期：月/日"},
    {"name": "monthday", "desc": "日期：月日"},
    {"name": "daymonth", "desc": "日期：日月"},
]

TICK_OPS = ["both", "in", "out", "off"]

# --------------------------------------------------------------------------
# Colours  (source: ligplot/grace defaults — @map color N)
# --------------------------------------------------------------------------

COLORS = [
    {"index": 0, "name": "white", "rgb": [255, 255, 255]},
    {"index": 1, "name": "black", "rgb": [0, 0, 0]},
    {"index": 2, "name": "red", "rgb": [255, 0, 0]},
    {"index": 3, "name": "green", "rgb": [0, 255, 0]},
    {"index": 4, "name": "blue", "rgb": [0, 0, 255]},
    {"index": 5, "name": "yellow", "rgb": [255, 255, 0]},
    {"index": 6, "name": "brown", "rgb": [188, 143, 143]},
    {"index": 7, "name": "grey", "rgb": [220, 220, 220]},
    {"index": 8, "name": "violet", "rgb": [148, 0, 211]},
    {"index": 9, "name": "cyan", "rgb": [0, 255, 255]},
    {"index": 10, "name": "magenta", "rgb": [255, 0, 255]},
    {"index": 11, "name": "orange", "rgb": [255, 165, 0]},
    {"index": 12, "name": "indigo", "rgb": [114, 33, 188]},
    {"index": 13, "name": "maroon", "rgb": [103, 7, 72]},
    {"index": 14, "name": "turquoise", "rgb": [64, 224, 208]},
    {"index": 15, "name": "green4", "rgb": [0, 139, 0]},
]

# --------------------------------------------------------------------------
# Fonts  (source: @map font in every example .agr)
# --------------------------------------------------------------------------

FONTS = [
    {"index": 0, "name": "Times-Roman"}, {"index": 1, "name": "Times-Italic"},
    {"index": 2, "name": "Times-Bold"}, {"index": 3, "name": "Times-BoldItalic"},
    {"index": 4, "name": "Helvetica"}, {"index": 5, "name": "Helvetica-Oblique"},
    {"index": 6, "name": "Helvetica-Bold"}, {"index": 7, "name": "Helvetica-BoldOblique"},
    {"index": 8, "name": "Courier"}, {"index": 9, "name": "Courier-Oblique"},
    {"index": 10, "name": "Courier-Bold"}, {"index": 11, "name": "Courier-BoldOblique"},
    {"index": 12, "name": "Symbol"}, {"index": 13, "name": "ZapfDingbats"},
]

# --------------------------------------------------------------------------
# Expression functions  (source: pars.c / y.tab.c token tables)
# --------------------------------------------------------------------------

EXPRESSION_FUNCTIONS = {
    "trig": ["sin", "cos", "tan", "asin", "acos", "atan"],
    "exp/log": ["exp", "ln", "log"],
    "misc": ["sqrt", "abs", "floor", "ceil", "round", "trunc", "mod"],
    "random": ["rand", "irand"],
    "aggregate": ["min", "max", "imin", "imax", "sum", "avg"],
    "note": ("用于 `-pexec` / `@pexec` 表达式、以及 'Evaluate expression' 功能。"
             "QtGrace 还接受简单 LaTeX 片段（写作 $$\\alpha$$）用于文本标签。"),
}

# --------------------------------------------------------------------------
# Output devices  (source: register_*_drv in rstdrv/psdrv/svgdrv/mifdrv/pdfdrv)
# --------------------------------------------------------------------------

DEVICES = [
    {"name": "PNG", "ext": "png", "vector": False, "works": True,
     "note": "本构建固定输出 792×612 像素，无法改尺寸"},
    {"name": "SVG", "ext": "svg", "vector": True, "works": True, "note": "推荐用于投稿"},
    {"name": "EPS", "ext": "eps", "vector": True, "works": True, "note": "期刊常接受"},
    {"name": "PS", "ext": "ps", "vector": True, "works": True, "note": ""},
    {"name": "JPEG", "ext": "jpg", "vector": False, "works": True, "note": "有损"},
    {"name": "PNM", "ext": "pnm", "vector": False, "works": True, "note": "文件很大"},
    {"name": "MIF", "ext": "mif", "vector": True, "works": True, "note": "FrameMaker"},
    {"name": "PDF", "ext": "pdf", "vector": True, "works": False,
     "note": "本构建会挂起，已禁用"},
    {"name": "HPNG", "ext": "png", "vector": False, "works": False,
     "note": "本构建无输出，已禁用"},
]

# --------------------------------------------------------------------------
# Worked recipes
# --------------------------------------------------------------------------

RECIPES = [
    {
        "name": "折线图 + 图例",
        "ask": "画一条 y=sin(x) 的曲线，x 从 0 到 2π",
        "call": {
            "tool": "make_plot",
            "arguments": {
                "output_path": "D:/figs/sine.png",
                "device": "PNG",
                "width_cm": 8.6, "height_cm": 6.4,
                "graphs": [{
                    "title": "Sine",
                    "world": [0, -1.1, 6.2832, 1.1],
                    "xaxis": {"label": "x (rad)", "tickMajor": 1.5708, "tickMinor": 0.7854, "tickPrec": 2},
                    "yaxis": {"label": "y", "tickMajor": 0.5, "tickMinor": 0.25, "tickPrec": 2},
                    "legend": {"pos": [0.62, 0.8]},
                    "sets": [{"legend": "sin(x)", "lineType": 1, "lineColor": 2, "symbol": 0,
                              "data": [[0, 0], [1.5708, 1], [3.1416, 0], [4.7124, -1], [6.2832, 0]]}],
                }],
            },
        },
    },
    {
        "name": "误差棒图",
        "ask": "带 y 方向误差棒的实验数据",
        "call": {
            "tool": "make_plot",
            "arguments": {
                "output_path": "D:/figs/errbars.png",
                "graphs": [{
                    "world": [0, 0, 6, 10],
                    "xaxis": {"label": "time (s)", "tickMajor": 1},
                    "yaxis": {"label": "signal", "tickMajor": 2},
                    "sets": [{"type": "xydy", "legend": "measured", "lineType": 1,
                              "lineColor": 3, "symbol": 1, "symbolSize": 0.5,
                              "data": [[0, 1.0, 0.3], [1, 2.4, 0.4], [2, 5.1, 0.5],
                                       [3, 7.6, 0.4], [4, 8.9, 0.3], [5, 9.4, 0.2]]}],
                }],
            },
        },
    },
    {
        "name": "柱状图",
        "ask": "画柱状图",
        "call": {
            "tool": "make_plot",
            "arguments": {
                "output_path": "D:/figs/bars.png",
                "graphs": [{
                    "graph_type": "CHART",
                    "title": "Bar chart",
                    "world": [0, 0, 5, 10],
                    "xaxis": {"label": "category", "tickMajor": 1, "bar": True},
                    "yaxis": {"label": "value", "tickMajor": 2},
                    "sets": [{
                        "type": "bar",
                        "symbolSize": 3,
                        "symbolFillColor": 4,
                        "symbolColor": 1,
                        "symbolLinewidth": 1,
                        "data": [[0.5, 3], [1.5, 6], [2.5, 4], [3.5, 8], [4.5, 5]],
                    }],
                }],
            },
        },
        "note": ("柱宽由 symbolSize 决定：实际宽度 = 0.01 × symbolSize（视口比例）。"
                 "symbolSize=3 约为视口宽的 3%。柱子填充用 symbolFillColor，"
                 "描边用 symbolColor + symbolLinewidth；bar 类数据集自动不画连接线。"),
    },
    {
        "name": "双对数图",
        "ask": "画 log-log 图",
        "call": {
            "tool": "make_plot",
            "arguments": {
                "output_path": "D:/figs/loglog.svg",
                "device": "SVG",
                "graphs": [{
                    "world": [1, 0.1, 1000, 100],
                    "xaxis": {"label": "x", "scale": "log", "tickMajor": 10, "tickMinor": 2},
                    "yaxis": {"label": "y", "scale": "log", "tickMajor": 10, "tickMinor": 2},
                    "sets": [{"lineType": 1, "lineColor": 1, "symbol": 1, "symbolSize": 0.5,
                              "data": [[1, 1], [10, 5], [100, 22], [1000, 90]]}],
                }],
            },
        },
    },
    {
        "name": "多面板",
        "ask": "一张图里放两个并排的图",
        "call": {
            "tool": "make_plot",
            "arguments": {
                "output_path": "D:/figs/panels.png",
                "width_cm": 17.6, "height_cm": 6.4,
                "graphs": [
                    {"title": "left", "view": [0.12, 0.16, 0.44, 0.84],
                     "world": [0, 0, 1, 1],
                     "xaxis": {"label": "x", "tickMajor": 0.5},
                     "yaxis": {"label": "y", "tickMajor": 0.5},
                     "sets": [{"lineType": 1, "lineColor": 1, "symbol": 1, "symbolSize": 0.6,
                               "data": [[0, 0], [0.5, 0.8], [1, 1]]}]},
                    {"title": "right", "view": [0.64, 0.16, 0.96, 0.84],
                     "world": [0, 0, 1, 1],
                     "xaxis": {"label": "x", "tickMajor": 0.5},
                     "yaxis": {"label": "y", "tickMajor": 0.5},
                     "sets": [{"lineType": 1, "lineColor": 2, "symbol": 2, "symbolSize": 0.6,
                               "data": [[0, 1], [0.5, 0.4], [1, 0]]}]},
                ],
            },
        },
        "note": ("每个 panel 一个 graphs 条目，用 view 指定它在页面上的矩形"
                 "（归一化 [xmin,ymin,xmax,ymax]）。注意两点："
                 "① 每个图默认在左右两侧都画 y 轴刻度标签，所以两个面板之间要留 0.15 以上间隔；"
                 "② 最左侧面板的 view.xmin 不要小于 0.10，否则 y 轴标签会被页边裁掉。"),
    },
]


# --------------------------------------------------------------------------
# Beyond the basics — features that are easy to miss
# --------------------------------------------------------------------------

ADVANCED = [
    {"key": "sets[].fill", "syntax": "@    s0 fill type 2 / fill color 7",
     "what": "填充区域。type 1=polygon（沿数据闭合），2=baseline（填到基线）。",
     "use_when": "画置信带、阴影区、直方图填充。"},
    {"key": "sets[].baseline", "syntax": "@    s0 baseline type 0 / baseline on",
     "what": "填充的锚定基线（type 0=0 值，1=数据最小，2=数据最大，3=图最小，4=图最大，5=平均）。",
     "use_when": "填充不是从 y=0 起算时。"},
    {"key": "sets[].dropline", "syntax": "@    s0 dropline on",
     "use_when": "散点图希望每个点有一条竖线垂到 x 轴。"},
    {"key": "sets[].avalue", "syntax": "@    s0 avalue on / avalue type 2 / avalue prec 2",
     "what": "在每个数据点/柱子旁标出数值。type 2 最常用；offset 微调位置。",
     "use_when": "柱状图或少量数据点需要直接读数。"},
    {"key": "xaxis.zero / yaxis.zero", "syntax": "@    xaxis type zero true",
     "what": "把坐标轴画到 0 的位置（十字轴）。注意：真正开关是 `type zero true`，"
             "`zeroxaxis` 块只是配置零轴的刻度。",
     "use_when": "数据跨正负，希望轴线穿过原点。"},
    {"key": "xaxis.alt / yaxis.alt", "syntax": "@    altxaxis tick on",
     "what": "在对侧（上/右）也画刻度。",
     "use_when": "多面板对齐、或希望四边都有刻度。"},
    {"key": "annotations", "syntax": "@with string / @with line ... @line def",
     "what": "自由文字与线/箭头标注。text 用 `string def \"…\"` 提交；"
             "line/arrow 必须在块末尾顶格写 `@line def` 才会生效。",
     "use_when": "加说明文字、指示箭头、辅助线。"},
    {"key": "timestamp", "syntax": "@timestamp on / @timestamp def \"…\"",
     "what": "页面上盖时间戳。format 可给任意字符串。",
     "use_when": "流水线出图需要溯源标记。"},
    {"key": "annotations: ellipse / box", "syntax": "@with ellipse … @ellipse def",
     "what": "椭圆与矩形标注。与 line 一样，必须由顶格的 @ellipse def / @box def 提交。",
     "use_when": "圈出感兴趣区域、加色块背景。"},
    {"key": "graphs[].frame", "syntax": "@    frame type 0 / frame color 8",
     "what": "图区边框。type 0=无、1=线框、2=背景填充；可设颜色/线宽/背景色。",
     "use_when": "希望图区有醒目边框或底色。"},
    {"key": "graphs[].autoscale", "syntax": "@g0 autoscale type AUTO",
     "what": "自动缩放策略：AUTO / NONE / XAXES / YAXES / XYAXES。",
     "use_when": "希望保留自动缩放，而不是被 world 锁死。"},
    {"key": "graphs[].worldStack", "syntax": "@    stack world xmin, xmax, ymin, ymax",
     "what": "世界坐标栈，每图最多 20 组。**顺序是 (xmin,xmax,ymin,ymax)，"
             "和 world 的 (xmin,ymin,xmax,ymax) 不同**。",
     "use_when": "一个图需要在多套轴范围之间切换。"},
    {"key": "graphs[].znorm", "syntax": "@    znorm 1",
     "what": "颜色映射集合（xycolor / xyvmap）的 z 归一化开关。",
     "use_when": "用颜色表达第三维时。"},
    {"key": "legend.box", "syntax": "@    legend box color / fill color / linewidth",
     "what": "图例框的颜色、填充色、线宽、线型。",
     "use_when": "让图例框配色与图一致或更醒目。"},
    {"key": "sets[].comment / symbolSkip / symbolChar", "syntax": "@    s0 comment \"…\"",
     "what": "数据集备注、符号跳过间隔、用字符当符号。",
     "use_when": "数据点太密时跳着画符号；或用字符做标记。"},
    {"key": "top-level defaults", "syntax": "@default linewidth / color / font / …",
     "what": "全局默认线宽/线型/颜色/字体/符号尺寸，未显式设置的对象都继承它。",
     "use_when": "统一整张图的基础样式。"},
    {"key": "description / date", "syntax": "@description \"…\" / @reference date 0",
     "what": "工程描述文本，以及日期轴处理（wrap / wrap year）。",
     "use_when": "时间轴出图，或需要在工程里留说明。"},
]

# 踩过的坑：这些写错了不会报错，只是**静默不生效**
SILENT_TRAPS = [
    "`@with line` 块末尾漏掉顶格的 `@line def` —— 解析通过，但线**完全不画**。",
    "`xaxis type zero true` 才是零轴开关；只写 `zeroxaxis tick on` 不会画出零轴。",
    "bar 类数据集忘了 `lineType=0` —— 柱顶会被一条折线连起来。",
    "柱宽由 `symbolSize` 决定（bw = 0.01*symbolSize），不是 x 间距。",
    "多图时漏掉 `@gN on` —— 只有 g0 会渲染。",
    "`.agr` 里写行内 `#` 注释 —— 整行被判语法错误。",
    "`@with ellipse` / `@with box` 与 line 一样，漏掉顶格的 `@ellipse def` / `@box def` 就不画。",
    "`stack world` 的参数顺序是 (xmin, xmax, ymin, ymax)，与 `world` 不同，写反了图会跑飞。",
]

# --------------------------------------------------------------------------
# Runtime limitations of this particular build
# --------------------------------------------------------------------------

LIMITATIONS = [
    "⚠️ 头号陷阱：**默认打印机不能是不可达的网络打印机**。QtGrace 的 -hardcopy 会构造 "
    "QPrinter，也就是向 Windows 要默认打印机；如果它是网络打印机且当下不可达"
    "（打印机关机、笔记本离开办公室、VPN 断了），Windows 会弹出模态框"
    "「正在等待打印机连接…」并永久阻塞，进程永不退出。\n"
    "实测：默认=HP 网络打印机 → 挂起；默认=Microsoft Print to PDF → 0.6 s 出图。\n"
    "解法：① 把默认打印机换成本地设备；② 用 printer_guard（默认开启），"
    "渲染期间临时切到本地打印机再还原。CLI：`--printers` 体检、`--no-printer-guard` 关闭。",
    "⚠️ **PNG / JPEG 会段错误（SIGSEGV）**。这两个是唯一走 Qt 自带光栅引擎的设备；"
    "其余（SVG/EPS/PS/MIF/PNM）走 Grace 原生驱动。\n"
    "实测随包 41 个示例：**SVG 41/41 全过；PNG 有 7 个崩溃**"
    "（charts / logit / pie / terr / tstack / txyr / xyz）。\n"
    "这 7 个换任意非 Qt 光栅设备都正常。**要稳就用 SVG/EPS/PS**，"
    "需要位图时先出矢量再转换。",
    "-hardcopy 是批处理开关（它把 gracebat 置为 TRUE），不会创建窗口。",
    "-printfile 在本构建里被忽略：输出文件 = <输入 agr 所在目录>/<输入 agr 主名>.<设备扩展名>。",
    "PNG 光栅尺寸固定 792×612 像素，@page size 与 -fixed w h 都不生效；要高清请用矢量格式。",
    "设备 PDF 会挂起、HPNG 无输出，两者均已禁用。",
    "随包模板 templates/Default.agr 用了行内 # 注释，本构建解析器不接受，"
    "因此启动时 stdout 会出现一批无害的 'syntax error:' —— 判断成功只看产物文件。",
    ".agr 里不要写行内 # 注释，整行会被判为语法错误。",
]


def sections() -> list:
    return ["overview", "graph_types", "set_types", "symbols", "line_types",
            "axes", "colors", "fonts", "expressions", "devices",
            "advanced", "silent_traps", "recipes", "limitations", "all"]


def get_section(name: str) -> dict:
    """Return one catalogue section (or everything)."""
    name = (name or "overview").strip().lower()
    data = {
        "overview": OVERVIEW,
        "graph_types": GRAPH_TYPES,
        "set_types": SET_TYPES,
        "symbols": SYMBOLS,
        "line_types": LINE_TYPES,
        "axes": {"tick_formats": TICK_FORMATS, "tick_ops": TICK_OPS,
                 "scales": ["normal (linear)", "log", "reciprocal"]},
        "colors": COLORS,
        "fonts": FONTS,
        "expressions": EXPRESSION_FUNCTIONS,
        "devices": DEVICES,
        "advanced": ADVANCED,
        "silent_traps": SILENT_TRAPS,
        "recipes": RECIPES,
        "limitations": LIMITATIONS,
    }
    if name in ("all", "*"):
        return data
    if name not in data:
        return {"error": f"unknown section '{name}'", "available": sections()}
    return {name: data[name]}
