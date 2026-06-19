# pyside6-mcp

Playwright-style MCP server for PySide6 apps — lets AI assistants see, control, and debug your Python desktop GUI without modifying your app's source code.

```
AI assistant → MCP tools → pyside6-mcp server → HTTP bridge → PySide6 app
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

The MCP server (stdio) and the in-app bridge are separate:

| Component | Where it runs | Needs PySide6? |
|-----------|---------------|----------------|
| **MCP server** (`pyside6-mcp`) | AI client's MCP process | No |
| **Bridge** (`python -m pyside6_mcp …`) | Inside your PySide6 app | Yes |

### Target app setup (required)

Install pyside6-mcp into **the app's venv** so the bridge module is importable:

```bash
cd your-pyside6-project
uv add --dev "pyside6-mcp @ git+https://github.com/com55/pyside6-mcp"
```

### MCP server — Claude Code

#### Option 1 — Plugin (MCP + skill in one command)

```bash
claude plugin install github:com55/pyside6-mcp
```

Installs the MCP server and the companion skill automatically.

#### Option 2 — MCP server only

Register with Claude Code — pick the scope that fits:

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

### MCP server — Cursor

Add the server to your MCP config. Requires [uv](https://docs.astral.sh/uv/) on `PATH`.

**Global** (available in every project — recommended):

| OS | Config file |
|----|-------------|
| Windows | `%USERPROFILE%\.cursor\mcp.json` |
| macOS / Linux | `~/.cursor/mcp.json` |

**Project** (committed with the repo): `.cursor/mcp.json`

```json
{
  "mcpServers": {
    "pyside6": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/com55/pyside6-mcp", "pyside6-mcp"]
    }
  }
}
```

Optional custom port:

```json
"env": { "PYSIDE6_MCP_PORT": "7890" }
```

After saving, reload MCP servers in **Cursor Settings → MCP** (toggle off/on or restart Cursor).

See [`examples/cursor-mcp-config.json`](examples/cursor-mcp-config.json) for a copy-paste template.

### MCP server — VS Code / GitHub Copilot

VS Code MCP uses the same stdio format. Add to your user or workspace MCP config
(**Settings → search "MCP" → Edit in settings.json**, or `.vscode/mcp.json` depending on your setup):

```json
{
  "mcpServers": {
    "pyside6": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/com55/pyside6-mcp", "pyside6-mcp"]
    }
  }
}
```

### MCP server — other clients (Windsurf, Cline, OpenCode, …)

Any MCP client that supports **stdio** servers can use this config block:

```json
{
  "mcpServers": {
    "pyside6": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/com55/pyside6-mcp", "pyside6-mcp"],
      "env": {
        "PYSIDE6_MCP_PORT": "7890"
      }
    }
  }
}
```

Place it wherever that client expects MCP config (user-level or project-level).
See [`examples/mcp-config.json`](examples/mcp-config.json) for the canonical template.

> **Note:** `uvx` downloads and runs the MCP server in an isolated env — PySide6 is **not**
> required there. PySide6 is only needed in the target app's venv (bridge setup above).

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

### From your AI assistant

Once the app is running with the bridge active, ask your assistant:

> "Screenshot the app and click the Apply button"
> "Why is the checkbox disabled? Inspect its state."
> "Fill in the form and submit it"
> "Show me the last 20 log lines from the app"

Your assistant uses the `launch_app`, `screenshot`, `get_widget_tree`, `find_widget`, `click`, `type_text`, `get_logs`, and other tools automatically.

`launch_app` returns only when the UI is ready (a visible top-level window that has been quiet for at least 500 ms), not merely when the bridge HTTP server is up. Default `timeout` is 30 seconds.

## Tools

| Tool | Description |
|------|-------------|
| `launch_app(command, cwd, port, timeout)` | Launch app and wait for UI readiness |
| `wait_until_ready(timeout, quiet_ms)` | Wait for UI readiness on an already-running app |
| `wait_for_idle(timeout, quiet_ms)` | Wait until UI has been quiet after an action |
| `get_app_status(port)` | Process + bridge health; detects likely modal blocks |
| `stop_app(port)` | Stop a launched app |
| `screenshot(widget_id?)` | Capture window or specific widget (modal/active-window aware) |
| `get_widget_tree()` | Full widget hierarchy with IDs |
| `get_widget_info(widget_id)` | Detailed properties of one widget |
| `get_app_state()` | Active window, focus, screen info |
| `find_widget(class_name?, object_name?, text?, visible?)` | Search widgets |
| `click(widget_id?, x?, y?, button?)` | Mouse click |
| `double_click(widget_id, x?, y?)` | Double click |
| `type_text(text, widget_id?)` | Keyboard input |
| `press_key(key)` | Named key: enter, escape, tab, up/down, f5, … |
| `scroll(dy, widget_id?, dx?)` | Scroll wheel |
| `list_actions()` | List QAction menu/toolbar items |
| `trigger_action(name?, text?)` | Trigger a QAction without clicking menus |
| `get_logs(n?)` | Recent Python log records |
| `get_app_output(port?, n?)` | Raw stdout/stderr from launched app |
| `eval_python(code)` | Execute Python inside the app process |

## Claude Code Skill

A companion skill is included at [`skills/pyside6-mcp/SKILL.md`](skills/pyside6-mcp/SKILL.md). It tells Claude when and how to use these tools automatically — no need to explain the workflow every time.

Install it by copying the skill folder to your Claude skills directory:

```bash
# macOS / Linux
cp -r skills/pyside6-mcp ~/.claude/skills/

# Windows (PowerShell)
Copy-Item -Recurse skills\pyside6-mcp "$env:USERPROFILE\.claude\skills\"
```

Once installed, Claude will automatically read the skill and use the correct workflow whenever you ask it to debug or interact with a PySide6 app.

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

- [`examples/test_app.py`](examples/test_app.py) — minimal PySide6 app to verify the bridge
- [`examples/mcp-config.json`](examples/mcp-config.json) — generic MCP config (Cursor, VS Code, Windsurf, …)
- [`examples/cursor-mcp-config.json`](examples/cursor-mcp-config.json) — Cursor-specific template
- [`examples/claude-mcp-config.json`](examples/claude-mcp-config.json) — same format, kept for reference

## License

MIT
