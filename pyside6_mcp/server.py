"""
FastMCP server for pyside6-mcp.

Run as MCP server:
    pyside6-mcp                          # default port 7890
    PYSIDE6_MCP_PORT=8000 pyside6-mcp   # custom port
"""
import json
import os
import shlex
import subprocess
import sys
import time

import httpx
from fastmcp import FastMCP
from fastmcp.utilities.types import Image

PORT = int(os.environ.get("PYSIDE6_MCP_PORT", "7890"))
BRIDGE = f"http://127.0.0.1:{PORT}"

# pid → Popen, keyed by port so multiple apps on different ports are supported
_procs: dict[int, subprocess.Popen] = {}

mcp = FastMCP(
    "pyside6-mcp",
    instructions=(
        "Controls and inspects a running PySide6 application. "
        "The target app must have `install_bridge()` called before app.exec(). "
        "Always start with screenshot() + get_widget_tree() to orient yourself. "
        "Use widget IDs from the tree for subsequent operations."
    ),
)


def _get(path: str, **params) -> dict:
    try:
        r = httpx.get(f"{BRIDGE}{path}", params={k: v for k, v in params.items() if v is not None}, timeout=10)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        raise RuntimeError(
            f"Cannot reach bridge on port {PORT}. "
            "Make sure install_bridge() was called in the target app."
        )


def _post(path: str, data: dict) -> dict:
    try:
        r = httpx.post(f"{BRIDGE}{path}", json=data, timeout=10)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        raise RuntimeError(
            f"Cannot reach bridge on port {PORT}. "
            "Make sure install_bridge() was called in the target app."
        )


# ---------------------------------------------------------------------------
# Visual / inspection tools
# ---------------------------------------------------------------------------

@mcp.tool()
def screenshot(widget_id: str | None = None) -> Image:
    """
    Capture a screenshot of the app window (or a specific widget by ID).
    Returns the image so you can see the current UI state.
    Call this first to orient yourself.
    """
    data = _get("/screenshot", widget_id=widget_id)
    import base64
    return Image(data=base64.b64decode(data["image"]), format="png")


@mcp.tool()
def get_widget_tree() -> str:
    """
    Get the full widget hierarchy of all visible windows as JSON.
    Each widget has: id, class, object_name, visible, enabled, geometry, text, children.
    Use widget IDs from this tree in other tools.
    """
    return json.dumps(_get("/widgets"), indent=2)


@mcp.tool()
def get_widget_info(widget_id: str) -> str:
    """
    Get detailed properties and state of a specific widget.
    Includes geometry, text, enabled/visible state, and dynamic Qt properties.
    """
    return json.dumps(_get(f"/widget/{widget_id}"), indent=2)


@mcp.tool()
def get_app_state() -> str:
    """
    Get app-level state: active window, focused widget, screen info.
    Useful to understand focus before sending keyboard events.
    """
    return json.dumps(_get("/app"), indent=2)


@mcp.tool()
def find_widget(
    class_name: str | None = None,
    object_name: str | None = None,
    text: str | None = None,
    visible: bool | None = None,
) -> str:
    """
    Search for widgets by class, objectName, text content, or visibility.
    Returns a list of matching widgets with their IDs.
    Examples:
      find_widget(class_name="QPushButton")
      find_widget(text="Apply Mods")
      find_widget(object_name="enabledCheckBox")
    """
    body = {}
    if class_name is not None:
        body["class"] = class_name
    if object_name is not None:
        body["object_name"] = object_name
    if text is not None:
        body["text"] = text
    if visible is not None:
        body["visible"] = visible
    return json.dumps(_post("/find", body), indent=2)


# ---------------------------------------------------------------------------
# Control tools
# ---------------------------------------------------------------------------

@mcp.tool()
def click(
    widget_id: str | None = None,
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
) -> str:
    """
    Click a widget or screen coordinate.
    - widget_id only: clicks the center of that widget
    - widget_id + x/y: clicks at (x, y) relative to the widget's top-left
    - x/y only: clicks at (x, y) relative to the main window
    button: 'left' (default), 'right', or 'middle'
    """
    if widget_id:
        body: dict = {"button": button}
        if x is not None:
            body["x"] = x
        if y is not None:
            body["y"] = y
        return json.dumps(_post(f"/widget/{widget_id}/click", body))
    return json.dumps(_post("/click", {"x": x, "y": y, "button": button}))


@mcp.tool()
def double_click(widget_id: str, x: int | None = None, y: int | None = None) -> str:
    """Double-click a widget. Sends two rapid left-clicks."""
    body: dict = {"button": "left"}
    if x is not None:
        body["x"] = x
    if y is not None:
        body["y"] = y
    _post(f"/widget/{widget_id}/click", body)
    return json.dumps(_post(f"/widget/{widget_id}/click", body))


@mcp.tool()
def type_text(text: str, widget_id: str | None = None) -> str:
    """
    Type text into a widget (focuses it first) or the currently focused widget.
    For special characters use press_key instead.
    """
    if widget_id:
        return json.dumps(_post(f"/widget/{widget_id}/type", {"text": text}))
    # fallback: use find_widget to get focused widget, then type
    state = _get("/app")
    fw = state.get("focus_widget")
    if fw:
        return json.dumps(_post(f"/widget/{fw}/type", {"text": text}))
    return json.dumps({"error": "No focused widget. Provide widget_id."})


@mcp.tool()
def press_key(key: str) -> str:
    """
    Press a key on the currently focused widget.
    Named keys: enter, return, escape, tab, backtab, backspace, delete,
                up, down, left, right, space, home, end, pageup, pagedown,
                f1–f6
    Single characters: pass the character directly, e.g. 'a', 'A', '1'
    """
    return json.dumps(_post("/key", {"key": key}))


@mcp.tool()
def scroll(dy: int, widget_id: str | None = None, dx: int = 0) -> str:
    """
    Scroll a widget. dy > 0 scrolls down, dy < 0 scrolls up.
    dx: horizontal scroll (positive = right).
    widget_id: target widget; omit to scroll the main window.
    """
    body: dict = {"dy": dy, "dx": dx}
    if widget_id:
        body["widget_id"] = widget_id
    return json.dumps(_post("/scroll", body))


# ---------------------------------------------------------------------------
# Debugging / inspection tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_logs(n: int = 50) -> str:
    """
    Get the last n log messages captured from the app's Python logging system.
    Includes timestamp, level, logger name, and message.
    """
    return json.dumps(_get("/logs", n=n), indent=2)


@mcp.tool()
def eval_python(code: str) -> str:
    """
    Evaluate a Python expression or execute a statement inside the app process.
    Context provides:
      - app: QApplication instance
      - widgets: dict mapping widget_id → QWidget
    Examples:
      eval_python("app.activeWindow().windowTitle()")
      eval_python("widgets['3'].isEnabled()")
      eval_python("list(app.allWidgets())")
    WARNING: runs arbitrary code in the app — use only for debugging.
    """
    return json.dumps(_post("/eval", {"code": code}), indent=2)


@mcp.tool()
def launch_app(
    command: str,
    cwd: str | None = None,
    port: int = 7890,
    timeout: int = 15,
) -> str:
    """
    Launch a PySide6 app with the MCP bridge pre-injected, then wait until
    it's ready. After this returns, screenshot/click/etc. work immediately.

    command must invoke the pyside6_mcp launcher so the bridge auto-starts:
      "uv run python -m pyside6_mcp main.py"
      "python -m pyside6_mcp myapp.py"

    cwd: working directory for the app process.
    port: bridge port (default 7890, set PYSIDE6_MCP_PORT env to override).
    timeout: seconds to wait for the bridge to come up (default 15).

    One-time setup: pyside6-mcp must be installed in the app's venv:
      uv add --dev "pyside6-mcp @ file:///i:/_CodingWorkspace/pyside6-mcp"
    """
    bridge_url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "PYSIDE6_MCP_PORT": str(port)}

    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        shlex.split(command),
        cwd=cwd,
        env=env,
        creationflags=flags,
    )
    _procs[port] = proc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return json.dumps({"error": f"App exited early with code {proc.returncode}", "pid": proc.pid})
        try:
            r = httpx.get(f"{bridge_url}/app", timeout=1)
            if r.status_code == 200:
                return json.dumps({"ok": True, "pid": proc.pid, "app": r.json()})
        except httpx.ConnectError:
            pass
        time.sleep(0.4)

    proc.terminate()
    return json.dumps({"error": f"Bridge did not start within {timeout}s", "pid": proc.pid})


@mcp.tool()
def stop_app(port: int = 7890) -> str:
    """
    Stop a previously launched app (started via launch_app).
    """
    proc = _procs.pop(port, None)
    if proc is None:
        return json.dumps({"error": f"No app tracked on port {port}"})
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return json.dumps({"ok": True, "returncode": proc.returncode})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
