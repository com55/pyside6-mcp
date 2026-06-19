"""
FastMCP server for pyside6-mcp.

Run as MCP server:
    pyside6-mcp                          # default port 7890
    PYSIDE6_MCP_PORT=8000 pyside6-mcp   # custom port
"""
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from typing import IO

import httpx
from fastmcp import FastMCP
from fastmcp.utilities.types import Image

PORT = int(os.environ.get("PYSIDE6_MCP_PORT", "7890"))
BRIDGE = f"http://127.0.0.1:{PORT}"

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    """One keep-alive client reused across calls (avoids per-call TCP setup)."""
    global _client
    if _client is None:
        _client = httpx.Client(
            base_url=BRIDGE,
            timeout=10,
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        )
    return _client

# pid → Popen, keyed by port so multiple apps on different ports are supported
_procs: dict[int, subprocess.Popen] = {}
# port → open log file handle that the launched app writes stdout/stderr to
_proc_logs: dict[int, IO] = {}


def _app_log_path(port: int) -> str:
    """Deterministic file capturing the launched app's stdout/stderr."""
    return os.path.join(tempfile.gettempdir(), f"pyside6-mcp-app-{port}.log")


def _read_log_tail(port: int, n: int) -> str:
    """Return the last n lines of the launched app's captured output."""
    fh = _proc_logs.get(port)
    if fh is not None:
        try:
            fh.flush()
        except (ValueError, OSError):
            pass
    path = _app_log_path(port)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return "".join(lines[-n:]) if n > 0 else "".join(lines)

mcp = FastMCP(
    "pyside6-mcp",
    instructions=(
        "Controls and inspects a running PySide6 application, like Playwright for Qt GUIs.\n\n"
        "PREFERRED WAY TO START THE APP: call the launch_app() tool. It spawns the app "
        "with the bridge injected automatically (by monkey-patching QApplication), so the "
        "user's source code needs ZERO modification. Do NOT edit the user's code to add "
        "install_bridge() — that is only a fallback for when the user insists on starting "
        "the app themselves outside of this server.\n\n"
        "If the app is already running and reachable, you can skip launch_app(). "
        "After the app is up, always start with screenshot() + get_widget_tree() to orient "
        "yourself, then use the widget IDs from the tree for subsequent operations."
    ),
)


def _bridge_unreachable_error() -> RuntimeError:
    return RuntimeError(
        f"Cannot reach bridge on port {PORT}. "
        "Start the app with the launch_app() tool — it injects the bridge automatically, "
        "with no changes needed to the app's source code."
    )


def _bridge_http_error(exc: httpx.HTTPStatusError) -> RuntimeError:
    """Turn a bridge 4xx/5xx into a readable message, surfacing its error body."""
    detail = None
    try:
        detail = exc.response.json().get("error")
    except Exception:
        detail = exc.response.text.strip() or None
    suffix = f": {detail}" if detail else ""
    return RuntimeError(f"Bridge returned {exc.response.status_code}{suffix}")


_TRANSIENT_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
)


def _should_retry(exc: Exception) -> bool:
    """True for transient connection hiccups that warrant a quick retry."""
    return isinstance(exc, _TRANSIENT_EXC)


def _app_status_from(proc_alive: bool, exit_code, bridge_ok: bool, timed_out: bool) -> dict:
    """Combine process liveness with bridge responsiveness into one status dict."""
    return {
        "running": bool(proc_alive),
        "exit_code": exit_code,
        "bridge_responsive": bool(bridge_ok),
        "likely_modal_block": bool(proc_alive and timed_out and not bridge_ok),
    }


_RETRY_BACKOFFS = (0.1, 0.3, 0.6)


def _request_with_retry(method: str, path: str, **kwargs) -> httpx.Response:
    """Issue a request on the pooled client, retrying transient connection errors."""
    client = _get_client()
    last_exc: Exception | None = None
    for attempt in range(len(_RETRY_BACKOFFS) + 1):
        try:
            r = client.request(method, path, **kwargs)
            r.raise_for_status()
            return r
        except httpx.HTTPStatusError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _should_retry(exc) or attempt == len(_RETRY_BACKOFFS):
                break
            time.sleep(_RETRY_BACKOFFS[attempt])
    assert last_exc is not None
    raise last_exc


def _get(path: str, **params) -> dict:
    try:
        r = _request_with_retry(
            "GET", path, params={k: v for k, v in params.items() if v is not None}
        )
        return r.json()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.RemoteProtocolError):
        raise _bridge_unreachable_error()
    except httpx.HTTPStatusError as exc:
        raise _bridge_http_error(exc)


def _post(path: str, data: dict) -> dict:
    try:
        r = _request_with_retry("POST", path, json=data)
        return r.json()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.RemoteProtocolError):
        raise _bridge_unreachable_error()
    except httpx.HTTPStatusError as exc:
        raise _bridge_http_error(exc)


def _kill_proc_tree(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    """Terminate a process and all its children (e.g. uv → python → app)."""
    if proc.poll() is not None:
        return

    pid = proc.pid
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            proc.terminate()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if sys.platform != "win32":
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                proc.kill()
            proc.wait(timeout=timeout)


def _bridge_gone(exc: Exception) -> bool:
    """True when the bridge is no longer accepting connections."""
    return isinstance(exc, (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout))


def _request_app_quit(port: int, timeout: float = 5.0) -> dict:
    """Ask the app to quit gracefully via the bridge, then wait for it to stop."""
    bridge_url = f"http://127.0.0.1:{port}"
    try:
        r = httpx.post(f"{bridge_url}/quit", timeout=5)
        r.raise_for_status()
    except httpx.ConnectError:
        return {"bridge_reachable": False}
    except httpx.HTTPError as exc:
        if _bridge_gone(exc):
            return {"bridge_reachable": True, "bridge_stopped": True}
        return {"bridge_reachable": True, "quit_error": str(exc)}

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{bridge_url}/app", timeout=0.5)
        except httpx.HTTPError as exc:
            if _bridge_gone(exc):
                return {"bridge_reachable": True, "bridge_stopped": True, "quit": r.json()}
            raise
        time.sleep(0.2)

    return {"bridge_reachable": True, "bridge_stopped": False, "quit": r.json()}


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
def get_app_output(port: int = 7890, n: int = 200) -> str:
    """
    Get the last n lines of the launched app's real stdout/stderr: print()
    output, uncaught tracebacks, and Qt/console warnings.

    This is the app's actual console output, captured because launch_app
    redirects it to a log file. Use this to debug crashes or startup failures.
    For structured Python logging records use get_logs() instead.

    Only works for apps started via launch_app on this server.
    """
    if not os.path.exists(_app_log_path(port)):
        return json.dumps({
            "error": f"No captured output for port {port}. "
                     "The app must be started via launch_app for output capture."
        })
    return json.dumps({"port": port, "output": _read_log_tail(port, n)}, indent=2)


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
    timeout: int = 30,
) -> str:
    """
    Launch a PySide6 app with the MCP bridge pre-injected, then wait until
    the UI is ready. After this returns, screenshot/click/etc. work immediately.

    Ready means a visible top-level window that has been quiet (no layout/paint
    activity) for at least 500 ms — not merely that the bridge HTTP server is up.

    This is the preferred way to start the app. The bridge is injected by
    monkey-patching QApplication in the launcher, so the app's source code needs
    NO modification — never add install_bridge() to the user's code for this.

    command must invoke the pyside6_mcp launcher so the bridge auto-starts:
      "uv run python -m pyside6_mcp main.py"
      "python -m pyside6_mcp myapp.py"

    cwd: working directory for the app process.
    port: bridge port (default 7890, set PYSIDE6_MCP_PORT env to override).
    timeout: seconds to wait for UI readiness (default 30).

    The app's stdout/stderr is captured to a log file (see get_app_output()).

    One-time setup: pyside6-mcp must be installed in the app's venv:
      uv add --dev "pyside6-mcp @ file:///i:/_CodingWorkspace/pyside6-mcp"
    """
    bridge_url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "PYSIDE6_MCP_PORT": str(port)}

    # The child inherits our stdout otherwise, which on stdio transport is the
    # MCP JSON-RPC channel — any output would corrupt it. Redirect to a log file
    # instead of discarding it, so get_app_output() can surface the real output.
    log_path = _app_log_path(port)
    log_file = open(log_path, "w", encoding="utf-8")
    _proc_logs.pop(port, None)
    _proc_logs[port] = log_file

    popen_kwargs: dict = {
        "args": shlex.split(command, posix=(sys.platform != "win32")),
        "cwd": cwd,
        "env": env,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(**popen_kwargs)
    _procs[port] = proc

    deadline = time.monotonic() + timeout
    last_ready: dict | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return json.dumps({
                "error": f"App exited early with code {proc.returncode}",
                "pid": proc.pid,
                "log_file": log_path,
                "output": _read_log_tail(port, 50),
            })
        try:
            r = httpx.get(f"{bridge_url}/ready", timeout=1)
            if r.status_code == 200:
                last_ready = r.json()
                if last_ready.get("ready"):
                    return json.dumps({
                        "ok": True,
                        "pid": proc.pid,
                        "log_file": log_path,
                        "ready": last_ready,
                    })
        except (httpx.ConnectError, httpx.ConnectTimeout):
            pass
        time.sleep(0.4)

    if last_ready is not None:
        return json.dumps({
            "error": f"App started but UI not ready within {timeout}s",
            "pid": proc.pid,
            "log_file": log_path,
            "ready": last_ready,
            "output": _read_log_tail(port, 50),
        })
    _kill_proc_tree(proc)
    return json.dumps({
        "error": f"Bridge did not start within {timeout}s",
        "pid": proc.pid,
        "log_file": log_path,
        "output": _read_log_tail(port, 50),
    })


@mcp.tool()
def wait_until_ready(timeout: int = 30, quiet_ms: int = 500) -> str:
    """
    Wait until the app UI is ready: a visible top-level window that has been
    quiet (no layout/paint activity) for `quiet_ms` milliseconds.

    Use after an action that reloads or rebuilds the UI. Returns the readiness
    snapshot. Does not launch anything — the app must already be running.
    """
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        try:
            last = _get("/ready", quiet_ms=quiet_ms)
            if last.get("ready"):
                return json.dumps({"ok": True, "ready": last})
        except RuntimeError:
            pass
        time.sleep(0.3)
    return json.dumps({"ok": False, "error": f"UI not ready within {timeout}s", "ready": last})


@mcp.tool()
def wait_for_idle(timeout: float = 5.0, quiet_ms: int = 300) -> str:
    """
    Wait until the app's UI has been quiet (no layout/paint activity) for
    `quiet_ms` ms, or until `timeout` seconds elapse. Call after a click or
    action that triggers async work, before taking a screenshot.
    """
    deadline = time.monotonic() + timeout
    last_idle = 0.0
    while time.monotonic() < deadline:
        try:
            last_idle = float(_get("/idle").get("idle_ms", 0.0))
        except RuntimeError:
            break
        if last_idle >= quiet_ms:
            return json.dumps({"ok": True, "idle_ms": last_idle})
        time.sleep(0.1)
    return json.dumps({"ok": False, "idle_ms": last_idle, "timeout": timeout})


@mcp.tool()
def get_app_status(port: int = 7890) -> str:
    """
    Report combined health of a launched app: whether the process is alive,
    its exit code, whether the bridge responds, and whether the main thread is
    likely blocked by a modal dialog (process alive but bridge unresponsive).
    """
    proc = _procs.get(port)
    proc_alive = proc is not None and proc.poll() is None
    exit_code = None if proc is None else proc.poll()

    bridge_ok = False
    timed_out = False
    bridge_url = f"http://127.0.0.1:{port}"
    try:
        r = httpx.get(f"{bridge_url}/app", timeout=2)
        bridge_ok = r.status_code == 200
    except (httpx.ConnectTimeout, httpx.ReadTimeout):
        timed_out = True
    except httpx.HTTPError:
        bridge_ok = False

    status = _app_status_from(proc_alive, exit_code, bridge_ok, timed_out)
    if status["likely_modal_block"]:
        status["hint"] = (
            "The Qt main thread appears blocked — most likely a modal dialog "
            "(QDialog.exec()/QMessageBox) is running a nested event loop. "
            "Tools that marshal to the main thread will time out until it closes."
        )
    status["output"] = _read_log_tail(port, 30)
    return json.dumps(status, indent=2)


@mcp.tool()
def list_actions() -> str:
    """
    List all QActions (menu items, toolbar actions) in visible windows with their
    name, text, shortcut, and enabled/checked state.
    """
    return json.dumps(_get("/actions"), indent=2)


@mcp.tool()
def trigger_action(name: str | None = None, text: str | None = None) -> str:
    """
    Trigger a QAction directly by objectName (`name`) or visible label (`text`),
    without clicking through menus. Useful to avoid opening modal menus and to
    reach toolbar/menu actions reliably. Provide at least one of name/text.
    """
    body: dict = {}
    if name is not None:
        body["name"] = name
    if text is not None:
        body["text"] = text
    return json.dumps(_post("/action", body))


@mcp.tool()
def stop_app(port: int = 7890) -> str:
    """
    Stop a previously launched app (started via launch_app).
    """
    result: dict = {"ok": True, "port": port}
    quit_status = _request_app_quit(port)
    result["quit"] = quit_status

    proc = _procs.pop(port, None)
    if proc is not None:
        _kill_proc_tree(proc)
        result["pid"] = proc.pid
        result["returncode"] = proc.returncode

    # Close our writable handle but leave the file on disk so get_app_output()
    # can still surface the app's final output (e.g. shutdown tracebacks).
    log_file = _proc_logs.pop(port, None)
    if log_file is not None:
        try:
            log_file.close()
        except OSError:
            pass
        result["log_file"] = _app_log_path(port)
    elif not quit_status.get("bridge_stopped"):
        if not quit_status.get("bridge_reachable"):
            return json.dumps({"error": f"No app tracked on port {port} and bridge unreachable"})
        return json.dumps({"error": f"App on port {port} did not stop after quit request", **result})

    return json.dumps(result)


def main() -> None:
    # Suppress the FastMCP startup banner. On stdio transport it is printed to
    # stderr, which MCP clients (e.g. Cursor) surface as scary-looking [error]
    # log lines even though the server connects fine.
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
