# qtgrace-mcp

MCP (Model Context Protocol) server for **qtgrace** — the Qt-based scientific plotting tool
(a modern port of Grace/xmgrace, commonly used for GROMACS MD analysis plots).

Control qtgrace programmatically over its built-in TCP ServerMode protocol: plot data,
style axes/legends, export images (PNG/EPS/SVG/JPEG/BMP), and save `.agr` project files.

> **v0.2.0** — 已升级至 MCP SDK 2.0，修复多项 bug，添加线程安全与日志系统。

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

> **Known limitations (qtgrace v026, learned from the docs + examples):**
>
> 1. **Batch rendering (`-hardcopy`) is reliable only for the *original* `.agr`
>    files shipped with qtgrace.** Programmatically generated or modified `.agr`
>    (any structural change — graph type, added set, log axis) tends to make
>    qtgrace *hang* in `-hardcopy` mode rather than error out. The example
>    replication therefore renders the untouched example `.agr` files directly;
>    it does **not** generate new `.agr` from scratch for the advanced types.
> 2. **`-hardcopy` segfaults on 7 plot types** — `charts`, `logit`, `pie`,
>    `terr`, `tstack`, `txyr`, `xyz`. These are reported as `failed` and skipped.
> 3. **Four examples reference external `.dat` files not shipped in this Windows
>    build** — `chartebar`, `motif`, `symslines`, `xysize`. Detected up-front and
>    skipped with a clear message.
> 4. **TCP ServerMode is a *narrow* channel.** Sending Grace commands via
>    `qtgrace_exec` is reliable (GUI updates live), but *headless export* of the
>    advanced graph types (Chart/stacked/Polar/Pie) via `export_image` **hangs**.
>    Only `XY` and basic `bar` plots export headlessly through TCP. For advanced
>    types, view them in the GUI instead of exporting. (This is a qtgrace v026
>    headless-render limitation, not an MCP bug.)
>
> Net: the robust path for browsing every example is `render_all_examples`
> (→ `gallery/index.html`); the robust path for ad-hoc plotting is `plot_data`
> / `plot_and_export` for XY & simple bar, plus `qtgrace_exec` for interactively
> driving advanced types in the live GUI.

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
