# qtgrace-mcp

MCP (Model Context Protocol) server for **qtgrace** — the Qt-based scientific plotting tool
(a modern port of Grace/xmgrace, commonly used for GROMACS MD analysis plots).

**两条互补的路线：**

1. **TCP ServerMode** —— 控制运行中的 qtgrace GUI：实时画图、改样式、导出图像。
2. **批处理引擎** —— 由结构化数据从零生成 `.agr` 再 `-hardcopy` 渲染，
   **不需要 GUI 进程**，**零依赖**，能表达 TCP 通道覆盖不到的高级图型。

两条路共用同一份 `.agr` 生成器、打印机保护与位图降级，所以出图结果一致。

> **v0.4.0** — 合并批处理引擎：17 个 MCP 工具、能力目录、打印机根因修复、位图自动降级。
> 41 个自带示例在 SVG 与 PNG 下**全部可出图**。

## Features

- Full coverage of the qtgrace ServerMode wire protocol (commands 1–99)
- Plot multi-series data with titles, axis labels, legends, and axis ranges
- Journal-style defaults: borderless legend, outward ticks, no top/right tick marks
- Export to PNG / EPS / SVG / JPEG / BMP / TIFF / PDF (device auto-switch)
- Save complete `.agr` project files (reloadable and editable in qtgrace)
- PNG post-processing that removes qtgrace's rendering artifact (stray ticks above the
  plot frame on multi-series/large datasets), with optional numpy acceleration
- Batch fallback renderer via `-hardcopy` (qtgrace v026 ignores `-printfile`; output is written to cwd then renamed)
- Thread-safe TCP controller with logging
- Advanced command pass-through: `qtgrace_exec` sends raw Grace commands to a
  running qtgrace to unlock graph types (Chart/stacked/Polar/Pie), log axes,
  annotations/arrows, fills, legend control, multi-graph layouts — anything the
  data API (`plot_data`) cannot express

## Requirements

- Python 3.10+
- `mcp` Python SDK >= 2.0: `pip install "mcp[cli]"`
- `Pillow` (for PNG post-processing): `pip install pillow`
- `numpy` (optional, for faster PNG post-processing): `pip install numpy`
- [qtgrace](https://sourceforge.net/projects/qtgrace/) installed; point `QTGRACE_PATH`
  at the executable if it is not auto-detected.

## Install & Configure

### From source

```bash
# 基础安装
pip install -e .

# 含 numpy 加速 (推荐)
pip install -e ".[fast]"

# 含开发依赖 (pytest)
pip install -e ".[dev]"
```

### Or install dependencies manually

```bash
pip install "mcp>=2.0" pillow
# 可选: numpy 加速 PNG 后处理
pip install numpy
```

Register in your MCP client (e.g. Claude / Reasonix / Cursor):

```json
{
  "mcpServers": {
    "qtgrace-mcp": {
      "command": "python",
      "args": ["-m", "qtgrace_mcp.server"]
    }
  }
}
```

Or use the direct script path:

```json
{
  "mcpServers": {
    "qtgrace-mcp": {
      "command": "python",
      "args": ["/path/to/qtgrace_mcp/server.py"]
    }
  }
}
```

## Usage (tools)

| Tool | Description |
|------|-------------|
| `start_qtgrace` | Launch qtgrace in ServerMode (choose output device: PNG/EPS/PS/SVG/JPEG/BMP/TIFF) |
| `plot_data` | Send data and draw (multi-series, title, labels, legends, style, axis range) |
| `export_image` | Export current plot to image file |
| `plot_and_export` | One-shot plot + export (auto device switch, optional batch mode) |
| `set_scaling_mode` | Set autoscale mode (command 6) |
| `set_layout_mode` | Set graph layout (command 8) |
| `save_agr` | Save current project as `.agr` |
| `stop_qtgrace` | Shut down qtgrace |
| `qtgrace_status` | Query running status |
| `qtgrace_exec` | Send raw Grace commands to the running qtgrace (advanced graph types, log axes, annotations, fills, layouts) |
| `list_examples` | List `.agr` examples available in the qtgrace examples directory |
| `render_example` | Render one example `.agr` to an image (returns status + file size) |
| `render_all_examples` | Batch-render every example and build an HTML gallery |
| **`qtgrace_reference`** | **画图前先读。** 返回完整能力目录：图类型 / 19 种数据集 / 符号 / 线型 / 刻度格式 / 调色板 / 字体 / 输出设备 / 进阶特性 / 静默陷阱 / 现成配方 |
| **`make_plot`** | **无头出图主力。** 由结构化数据从零生成 `.agr` 并渲染，不经 TCP，覆盖高级图型 |
| **`describe_agr`** | 解析检查一份 `.agr`：语句数、图/数据集、数据行、已知会静默失效的写法 |
| **`qtgrace_capabilities`** | 环境体检：路径、能否出图、**打印机风险**、输出设备 |

### Quick example

```python
# (inside an MCP client, e.g. via the agent)
start_qtgrace(device="PNG")
plot_data(
    datasets=[[[0, 1, 2, 3, 4], [0, 1, 4, 9, 16]]],
    title="y = x^2", xlabel="Time (ns)", ylabel="RMSD (nm)",
    legends=["mutant"], xmin=0, xmax=200, ymin=0, ymax=20,
)
export_image(filename="plot", format="png")
save_agr(filename="plot.agr")
stop_qtgrace()
```

### Batch mode (no GUI process)

```python
plot_and_export(
    datasets=[[[0, 1, 2], [0, 1, 4]]],
    filename="batch_plot",
    title="Batch",
    format="png",
    use_batch=True,  # 使用 -hardcopy 模式 (cwd+rename 定位输出), 无需保持 GUI 进程
)
```

### Advanced command pass-through (`qtgrace_exec`)

When the data API (`plot_data`) is not enough — e.g. you need a stacked bar, a
log axis, an annotation/arrow, a fill, legend control, or a multi-graph layout —
send raw Grace commands to the running qtgrace over the TCP channel:

```python
start_qtgrace(device="PNG")
plot_data(datasets=[[[1,2,3,4],[3,5,2,6]]], title="base")
# 切到堆叠条形图 (Chart + stacked + bar sets)
qtgrace_exec("@ g0 type Chart\n@ g0 stacked true\n@ s0 type bar\n@ s1 type bar")
# 对数轴 / 文本标注
qtgrace_exec("@ xaxes scale Logarithmic")
qtgrace_exec('@ with string\n@ string on\n@ string def "peak"')
```

`qtgrace_exec` accepts one or more Grace commands (split on `\n`, or a whole
`.agr` fragment). The command stream is reliable and reflected in the GUI in
real time. See the **Known limitations** below for what can be *exported*
headlessly.

## Batch plotting engine (no GUI, no TCP)

`make_plot` 与上面的 TCP 工具**互补**：TCP 驱动运行中的 GUI，这条路直接生成 `.agr`
再调 `-hardcopy` 渲染，**不需要 GUI 进程**，而且能表达 `plot_data` 覆盖不到的东西。

**它是零依赖的** —— `agr.py` / `catalog.py` / `batch.py` / `printer.py` 只用标准库。
（只有 TCP 那半需要 `mcp` 与 `Pillow`。）

### 能力目录：让 AI 一次看懂能画什么

```python
qtgrace_reference(section="overview")      # 三步工作流 + 「要画什么→用哪个图型」对照表
qtgrace_reference(section="graph_types")   # XY / CHART / POLAR / POLARV2 / PIE
qtgrace_reference(section="set_types")     # 19 种数据集类型及列顺序
qtgrace_reference(section="advanced")      # 填充/数值标签/零轴/标注/时间戳/frame…
qtgrace_reference(section="silent_traps")  # 写错不报错、只是不生效的坑
```

所有枚举都**从 qtgrace 源码与 examples 逆向提取**，不是猜的。

### 从零生成并出图

```python
make_plot(spec_json=json.dumps({
    "output_path": "D:/figs/a.png",
    "graphs": [{
        "graph_type": "XY",
        "title": "阻尼振荡", "world": [0, -1.2, 6.3, 1.2],
        "xaxis": {"label": "x (rad)", "tickMajor": 1, "zero": True},
        "yaxis": {"label": "y", "tickMajor": 0.5},
        "sets": [{"type": "xy", "lineType": 1, "lineColor": 2,
                  "fill": {"type": 2, "color": 7},
                  "data": [[0,0], [1.57,1], [3.14,0], [4.71,-1], [6.28,0]]}],
    }],
}), save_agr=True)
```

```
出图成功: D:\figs\a.png  (PNG, 792x612)
工程文件: D:\figs\a.agr  (可在 qtgrace GUI 里打开微调)
```

`save_agr=True` 会留下 `.agr` 工程文件 —— 这是 **「AI 出图 + 人工调位置」** 的工作流：
AI 负责数据、坐标轴、样式，你在 qtgrace GUI 里拖标签、调位置，然后 File/Save。

### 命令行（不经过 MCP）

```bash
qtgrace-plot spec.json -o fig.png            # 从 JSON 规格出图
qtgrace-plot --agr old.agr -o fig.eps        # 渲染已有 .agr
qtgrace-plot --save-agr spec.json -o fig.png # 同时留下 .agr
qtgrace-plot --doctor                        # 探测能否出图（含打印机体检）
qtgrace-plot --printers                      # 列出打印机，标出网络设备
qtgrace-plot --reference advanced            # 查能力目录
```

### 实测覆盖

| 项目 | 结果 |
|---|---|
| qtgrace 自带 41 个示例 × SVG | **41/41** |
| qtgrace 自带 41 个示例 × PNG | **41/41**（7 个走位图降级） |
| `.agr` 指令覆盖率 | 41/46（89%，其余为轴/刻度子属性，本就能生成） |
| 单元测试 | 80 个通过 |

## Examples

- `examples/probe_server.py` — low-level TCP protocol probe (protocol validation)
- `examples/smoke_test.py` — MCP stdio handshake + tools/list smoke test (mcp 2.0)
- `examples/audit.py` — capability audit (formats, edge cases)

## Replicating the official qtgrace examples

The server can reproduce every plot shipped in qtgrace's `examples/` directory
(the `.agr` project files bundled with the binary) and emit a browsable HTML
gallery.

```bash
# 1. List what is available
python -c "from qtgrace_mcp.server import list_examples_dir; print(list_examples_dir())"

# 2. Render a single example -> gallery/bar.png
python -c "from qtgrace_mcp.server import render_example_file; print(render_example_file(r'D:\qtgrace_v026_Win7\examples', 'bar'))"

# 3. Render ALL examples + build gallery/index.html
python -m qtgrace_mcp.examples.build_gallery --format png
```

Or call the MCP tools from your client:

```python
list_examples()                    # lists *.agr in D:\qtgrace_v026_Win7\examples
render_example(name="bar")         # renders bar.agr -> .../rendered/bar.png
render_all_examples(format="png")  # renders every .agr + writes gallery/index.html
```

> **已解决的限制（v0.4.0 更正）：**
>
> 下面三条曾记录为 qtgrace v026 的固有限制。经过实测，它们**都不是 qtgrace 的问题**，
> 现在全部已修复 —— 41 个自带示例在 SVG 和 PNG 下**都能出图**。
>
> 1. ~~**程序生成的 `.agr` 会让 `-hardcopy` 挂起**~~ → **根因是默认打印机**。
>    qtgrace 的 `-hardcopy` 会构造 `QPrinter`，也就是向 Windows 索取默认打印机；
>    若默认打印机是**不可达的网络打印机**（打印机关机 / 笔记本离开办公室 / VPN 断），
>    Windows 会弹出模态框「正在等待打印机连接…」并**永久阻塞**。
>    实测交替切换默认打印机：HP 网络打印机 → 挂起；`Microsoft Print to PDF` → 0.6 s 出图；
>    切回 HP → 又挂起。**与 `.agr` 是生成的还是原装的无关。**
>    → 已由 `printer_guard` 修复（渲染期间临时切到本地打印机，结束后还原）。
> 2. ~~**`-hardcopy` 在 7 类图上段错误**~~ → **只有 PNG/JPEG 会**，因为它们是唯一走
>    **Qt 自带光栅引擎**的设备；SVG/EPS/PS/MIF/PNM 走 Grace 原生驱动，从不崩。
>    → 已由**位图降级**修复：崩溃时改用 PNM 渲染再转换。PNG 用纯标准库编码
>    （zlib + struct，不需要 Pillow），JPEG 用 Pillow。
>    `charts` / `logit` / `pie` / `terr` / `tstack` / `txyr` / `xyz` 现在都能出图。
> 3. ~~**4 个示例缺外部 `.dat` 文件**~~ → `chartebar` / `motif` / `symslines` / `xysize`
>    实测**都能正常渲染**（SVG 与 PNG 均通过）。
>
> 仍然成立的两条：
> 4. **TCP ServerMode 是窄通道**：`qtgrace_exec` 发命令可靠（GUI 实时更新），但
>    经 `export_image` **无头导出**高级图型（Chart/stacked/Polar/Pie）会挂起。
>    → 需要无头出图时请改用 `make_plot`（走文件 + `-hardcopy`，不经 TCP）。
> 5. **PNG 光栅尺寸固定 792×612 px**：`@page size` 与 `-fixed w h` 都不生效。
>    要高清请用矢量格式（SVG/EPS/PS）。

> **两条路线怎么选**
>
> | 需求 | 用哪个 |
> |---|---|
> | 浏览/复刻全部示例 | `render_all_examples` → `gallery/index.html` |
> | 无头出图、高级图型、批量 | **`make_plot`**（文件 + `-hardcopy`，不经 TCP） |
> | 交互式调图、实时看 GUI | `start_qtgrace` + `plot_data` + `qtgrace_exec` |
> | 想手工微调排版 | `make_plot(save_agr=true)` 留下 `.agr`，在 GUI 里打开 |

## Testing

```bash
# 单元测试 (无需 qtgrace)
pytest tests/test_server.py -v

# 端到端测试 (需要 qtgrace, 会实际启动进程)
python tests/test_e2e.py

# MCP 协议冒烟测试
python qtgrace_mcp/examples/smoke_test.py
```

## How it works

qtgrace exposes a TCP ServerMode: `qtgrace -ServerMode <write_port> <read_port>`.
The client sends 4-byte commands (PLOT_INFO=1, WRITE_DATAVEC=2, REDRAW=4, ...,
END_COMM=99) plus data payloads, and reads responses on the write port.
This server wraps that protocol into ergonomic MCP tools.

### Protocol flow

1. New TCP connection to both read/write ports
2. Send `initComm` (0) on read port, receive `initComm` on write port (handshake)
3. Send `readDataComm` (2) + payload length + payload on read port
4. Receive `endComm` (1) on write port
5. Close connection

Each operation creates a fresh connection; no persistent session state.

## Changelog

### v0.4.0 (2026-10-11)

**合并批处理引擎 —— 修掉此前记录的三条「已知限制」**

- 新增 `qtgrace_mcp/` 下的零依赖模块：`agr.py`（.agr 生成器）、`catalog.py`（能力目录）、
  `batch.py`（批处理渲染）、`printer.py`（打印机保护）、`cli.py`（命令行）
- 新增 4 个 MCP 工具（共 17 个）：`qtgrace_reference`、`make_plot`、`describe_agr`、
  `qtgrace_capabilities`
- 新增 `qtgrace-plot` 命令行入口（`--agr` / `--save-agr` / `--doctor` / `--printers` /
  `--reference`）
- **修复「程序生成的 .agr 会让 -hardcopy 挂起」** —— 根因是**默认打印机**：
  `-hardcopy` 会构造 `QPrinter` 并向 Windows 索取默认打印机，网络打印机不可达时
  Windows 弹模态框「正在等待打印机连接…」并永久阻塞。实测交替切换：HP 网络打印机 →
  挂起；`Microsoft Print to PDF` → 0.6 s 出图。与 `.agr` 是否生成无关。
  修复方式：`printer_guard` 渲染期间临时切到本地打印机，结束后还原
- **修复「7 类图段错误」** —— 只有 PNG/JPEG 会，因为它们是唯一走 Qt 自带光栅引擎的设备。
  修复方式：崩溃时改用 PNM（Grace 原生驱动）渲染再转换；PNG 用纯标准库编码
  （zlib + struct，无需 Pillow）
- **更正「4 个示例缺外部数据」** —— `chartebar` / `motif` / `symslines` / `xysize`
  实测都能正常渲染
- `.agr` 生成器覆盖 41/46 条指令（89%），含填充、数值标签、零轴、对侧轴、
  文字/线/箭头/椭圆/矩形标注、时间戳、frame、autoscale、worldStack、znorm、defaults
- 记录两个**静默陷阱**（写错不报错、只是不生效）：
  ① `@with line` / `@with box` / `@with ellipse` 必须由顶格的 `@line def` 等提交；
  ② 零轴开关是 `xaxis type zero true`，不是 `zeroxaxis tick on`
- 新增 80 个单元测试（合计 100+）；实测 41 个示例 × SVG/PNG 全部通过
- **修复 `QtGraceBatchRenderer.render()`** —— 它原先用 `build_batch_agr()` 生成
  **Grace 命令脚本**（`READ XY "…"` 那一串），却当 `.agr` 工程文件传给 qtgrace，
  于是静默不出图（`test_batch_renderer` 一直失败）。现在改走 `agr.build_agr()`
  生成真正的 `.agr`（数据内嵌），测试通过

### v0.3.0 (2026-08-23)

- 新增示例复刻能力: 渲染 qtgrace `examples/` 全部 `.agr` 并生成 HTML 画廊
- 新增模块级函数 `list_examples_dir` / `render_example_file` / `render_examples_dir` / `build_gallery_html`
- 新增 MCP 工具 `list_examples` / `render_example` / `render_all_examples`
- 新增 `qtgrace_mcp/examples/build_gallery.py` 一键复刻脚本
- **修复批渲染核心 bug**: qtgrace v026 忽略 `-printfile`, 旧 `render_agr`/`render`
  把 `@` 开头的项目文件喂给 `-batch` 必失败。统一改用 `-hardcopy` + 控制 cwd +
  事后 rename 的定位方式 (`_hardcopy_render`)
- 新增缺失外部数据预检, 避免 qtgrace 在缺数据时挂起
- `render_all_examples` 对崩溃/缺数据的示例标记为 `failed`/`missing`, 不阻塞整体
- 新增 `qtgrace_exec` 工具: 经 TCP 向运行中的 qtgrace 发送原始 Grace 命令, 解锁 Chart/stacked/Polar/Pie、对数轴、标注/箭头、填充、图例、多图布局等高级能力 (命令发送可靠, 高级类型无头导出会挂起, 已写入工具与文档说明)
- 研究 UsersGuide / Tutorial / QTGRACE_EXTENSIONS 文档与 examples 后, 在 README 记录关键环境约束: `-hardcopy` 仅对原始 `.agr` 可靠、TCP 为窄通道、7 类图无头导出崩溃/挂起

### v0.2.0 (2026-08-13)

- **Breaking**: 升级至 MCP SDK 2.0 (`FastMCP` → `MCPServer`, `run(transport='stdio')` → `run_stdio_async()`)
- 修复 `save_agr` 中正则表达式 bug (`altxaxis`/`altyaxis` 匹配错误)
- 修复 `audit.py` 导入错误 (`qtgrace_mcp_server` → `qtgrace_mcp.server`)
- 修复 `probe_server.py` 硬编码路径, 添加自动探测
- 添加线程安全 (`threading.Lock` 保护 TCP 会话和全局单例)
- 添加 logging 系统 (输出到 stderr)
- PNG 后处理添加 numpy 向量化加速 (可选依赖)
- `plot_and_export` 添加 `use_batch` 参数支持无 GUI 渲染
- 完善类型注解和错误处理
- 添加 20 个单元测试 + 端到端测试
- 升级 pyproject.toml: 版本 0.2.0, 可选依赖 `[fast]`/`[dev]`, pytest 配置

### v0.1.0

- 初始版本, 基于 MCP SDK 1.x

## License

MIT
