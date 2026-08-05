# qtgrace-mcp

MCP (Model Context Protocol) server for **qtgrace** — the Qt-based scientific plotting tool
(a modern port of Grace/xmgrace, commonly used for GROMACS MD analysis plots).

Control qtgrace programmatically over its built-in TCP ServerMode protocol: plot data,
style axes/legends, export images (PNG/EPS/SVG/JPEG/BMP), and save `.agr` project files.

## Features

- Full coverage of the qtgrace ServerMode wire protocol (commands 1–99)
- Plot multi-series data with titles, axis labels, legends, and axis ranges
- Journal-style defaults: borderless legend, outward ticks, no top/right tick marks
- Export to PNG / EPS / SVG / JPEG / BMP (device auto-switch)
- Save complete `.agr` project files (reloadable and editable in qtgrace)
- PNG post-processing that removes qtgrace's rendering artifact (stray ticks above the
  plot frame on multi-series/large datasets)
- Batch fallback renderer via `-hardcopy -batch`

## Requirements

- Python 3.10+
- `mcp` Python SDK: `pip install "mcp[cli]"`
- `Pillow` (for PNG post-processing): `pip install pillow`
- [qtgrace](https://sourceforge.net/projects/qtgrace/) installed; point `QTGRACE_PATH`
  at the executable if it is not auto-detected.

## Install & Configure

```bash
pip install "mcp[cli]" pillow
```

Register in your MCP client (e.g. Claude / Reasonix):

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
| `start_qtgrace` | Launch qtgrace in ServerMode (choose output device: PNG/EPS/PS/SVG/JPEG/BMP) |
| `plot_data` | Send data and draw (multi-series, title, labels, legends, style, axis range) |
| `export_image` | Export current plot to image file |
| `plot_and_export` | One-shot plot + export (auto device switch) |
| `set_scaling_mode` | Set autoscale mode (command 6) |
| `set_layout_mode` | Set graph layout (command 8) |
| `save_agr` | Save current project as `.agr` |
| `stop_qtgrace` | Shut down qtgrace |
| `qtgrace_status` | Query running status |

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
```

## Examples

- `examples/probe_server.py` — low-level TCP protocol probe (protocol validation)
- `examples/smoke_test.py` — MCP stdio handshake + tools/list smoke test
- `examples/audit.py` — capability audit (formats, edge cases)

## How it works

qtgrace exposes a TCP ServerMode: `qtgrace -ServerMode <write_port> <read_port>`.
The client sends 4-byte commands (PLOT_INFO=1, WRITE_DATAVEC=2, REDRAW=4, ...,
END_COMM=99) plus data payloads, and reads responses on the write port.
This server wraps that protocol into ergonomic MCP tools.

## License

MIT
