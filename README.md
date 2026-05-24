# pyside6-mcp

Playwright-style MCP server for PySide6 apps — lets Claude see, control, and debug your Python desktop GUI without modifying your app's source code.

```
Claude → MCP tools → pyside6-mcp server → HTTP bridge → PySide6 app
```

## Features

- **Screenshot** any window or widget
- **Inspect** the full widget tree (class, name, geometry, text, state)
- **Click, type, scroll, press keys** — full interaction
- **Find widgets** by class, objectName, or text content
- **Read Python logs** captured from the app
- **Run Python** inside the app process for advanced inspection
- **Launch and stop** the app directly from Claude

Zero changes to your app's source code required.

## Requirements

- Python 3.11+
- PySide6 6.6+
- [uv](https://docs.astral.sh/uv/) (recommended)
- Windows (tested), Linux/macOS (should work)

## Installation

Install pyside6-mcp into **your app's venv** (the target you want to debug):

```bash
# From PyPI (once published)
uv add --dev pyside6-mcp

# From GitHub
uv add --dev "pyside6-mcp @ git+https://github.com/com55/pyside6-mcp"
```

Register the MCP server with Claude Code — pick the scope that fits:

```bash
# user: available in every project (recommended for a debug tool)
claude mcp add -s user pyside6 -- uvx --from git+https://github.com/com55/pyside6-mcp pyside6-mcp

# project: only for the current project (saved to .mcp.json, can be committed)
claude mcp add -s project pyside6 -- uvx --from git+https://github.com/com55/pyside6-mcp pyside6-mcp

# local: only for the current project, not committed (default scope)
claude mcp add pyside6 -- uvx --from git+https://github.com/com55/pyside6-mcp pyside6-mcp
```

Optionally set a custom port with `-e`:

```bash
claude mcp add -s user -e PYSIDE6_MCP_PORT=7890 pyside6 -- uvx --from git+https://github.com/com55/pyside6-mcp pyside6-mcp
```

## Usage

### Launch your app through the bridge (no source changes needed)

```bash
# Instead of: python main.py
python -m pyside6_mcp main.py

# With uv
uv run python -m pyside6_mcp main.py
```

The bridge starts automatically on `http://127.0.0.1:7890`.

### Or: embed the bridge (optional, for always-on)

```python
# In your app's main(), before app.exec()
from pyside6_mcp import install_bridge
install_bridge()
```

### From Claude

Once the app is running with the bridge active, ask Claude:

> "Screenshot the app and click the Apply button"
> "Why is the checkbox disabled? Inspect its state."
> "Fill in the form and submit it"
> "Show me the last 20 log lines from the app"

Claude uses the `launch_app`, `screenshot`, `get_widget_tree`, `find_widget`, `click`, `type_text`, `get_logs`, and other tools automatically.

## Tools

| Tool | Description |
|------|-------------|
| `launch_app(command, cwd, port, timeout)` | Launch app and wait for bridge |
| `stop_app(port)` | Stop a launched app |
| `screenshot(widget_id?)` | Capture window or specific widget |
| `get_widget_tree()` | Full widget hierarchy with IDs |
| `get_widget_info(widget_id)` | Detailed properties of one widget |
| `get_app_state()` | Active window, focus, screen info |
| `find_widget(class_name?, object_name?, text?, visible?)` | Search widgets |
| `click(widget_id?, x?, y?, button?)` | Mouse click |
| `double_click(widget_id, x?, y?)` | Double click |
| `type_text(text, widget_id?)` | Keyboard input |
| `press_key(key)` | Named key: enter, escape, tab, up/down, f5, … |
| `scroll(dy, widget_id?, dx?)` | Scroll wheel |
| `get_logs(n?)` | Recent Python log records |
| `eval_python(code)` | Execute Python inside the app process |

## Architecture

```
pyside6_mcp/
├── bridge.py      # In-process HTTP server (runs inside the target app)
├── server.py      # FastMCP stdio server (Claude talks to this)
├── __init__.py    # Exports install_bridge()
└── __main__.py    # Launcher: python -m pyside6_mcp <script>
```

**Thread safety**: all Qt operations are marshaled to the main thread via `QApplication.postEvent` with a custom event type — the same mechanism Qt uses internally for cross-thread signals.

## Examples

See [`examples/test_app.py`](examples/test_app.py) for a minimal PySide6 app you can use to verify the setup.

## License

MIT
