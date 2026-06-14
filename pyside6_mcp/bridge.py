"""
In-app bridge for pyside6-mcp.

Usage (add to your app before app.exec()):
    from pyside6_mcp import install_bridge
    install_bridge()
"""
import base64
import json
import logging
import sys
import threading
import weakref
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QWidget

logger = logging.getLogger("pyside6_mcp.bridge")

_log_records: list[dict] = []
_widget_registry: dict[str, weakref.ref] = {}
_widget_by_ptr: dict[int, str] = {}


# ---------------------------------------------------------------------------
# Thread-safety: marshal Qt calls to the main thread via postEvent
# QTimer.singleShot fires in the *caller's* thread — postEvent is the
# correct cross-thread dispatch mechanism in Qt.
# ---------------------------------------------------------------------------

_CALL_EVENT_TYPE = QEvent.Type(QEvent.registerEventType())


class _CallEvent(QEvent):
    def __init__(self, func, sync: threading.Event, result: list) -> None:
        super().__init__(_CALL_EVENT_TYPE)
        self.func = func
        self.sync = sync
        self.result = result


class _MainProxy(QObject):
    """Lives in the main thread; receives _CallEvents and executes them."""

    def event(self, e: QEvent) -> bool:
        if e.type() == _CALL_EVENT_TYPE:
            assert isinstance(e, _CallEvent)
            try:
                e.result.append(("ok", e.func()))
            except Exception as exc:
                e.result.append(("err", exc))
            finally:
                e.sync.set()
            return True
        return super().event(e)


_proxy: _MainProxy | None = None


def _call_main(func, timeout: float = 5.0):
    result: list = []
    sync = threading.Event()
    QApplication.instance().postEvent(_proxy, _CallEvent(func, sync, result))
    if not sync.wait(timeout):
        raise TimeoutError("Qt main thread call timed out after 5 s")
    tag, val = result[0]
    if tag == "err":
        raise val
    return val


# ---------------------------------------------------------------------------
# Widget registry
# ---------------------------------------------------------------------------

def _widget_id(w: QWidget) -> str:
    ptr = id(w)
    if ptr in _widget_by_ptr:
        wid = _widget_by_ptr[ptr]
        if _widget_registry.get(wid, lambda: None)() is not None:
            return wid
    wid = str(len(_widget_registry))
    _widget_registry[wid] = weakref.ref(w)
    _widget_by_ptr[ptr] = wid
    return wid


def _resolve(wid: str) -> QWidget | None:
    ref = _widget_registry.get(wid)
    return ref() if ref else None


# ---------------------------------------------------------------------------
# Widget serialisation
# ---------------------------------------------------------------------------

def _widget_text(w: QWidget) -> str | None:
    for attr in ("text", "currentText", "toPlainText", "value", "title"):
        fn = getattr(w, attr, None)
        if callable(fn):
            try:
                return str(fn())
            except Exception:
                pass
    return None


def _widget_dict(w: QWidget, depth: int = 0, max_depth: int = 8) -> dict:
    try:
        g = w.geometry()
        d = {
            "id": _widget_id(w),
            "class": type(w).__name__,
            "object_name": w.objectName() or None,
            "visible": w.isVisible(),
            "enabled": w.isEnabled(),
            "geometry": {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()},
            "text": _widget_text(w),
            "tooltip": w.toolTip() or None,
        }
        if depth < max_depth:
            children = [c for c in w.children() if isinstance(c, QWidget) and c.isVisible()]
            if children:
                d["children"] = [_widget_dict(c, depth + 1, max_depth) for c in children]
        return d
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Log capture
# ---------------------------------------------------------------------------

class _LogCapture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        _log_records.append({
            "time": record.created,
            "level": record.levelname,
            "logger": record.name,
            "message": self.format(record),
        })
        if len(_log_records) > 500:
            _log_records.pop(0)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence request log
        pass

    def _send(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n)) if n else {}

    def do_GET(self):  # noqa: N802
        p = urlparse(self.path)
        qs = parse_qs(p.query)
        path = p.path
        try:
            if path == "/screenshot":
                wid = qs.get("widget_id", [None])[0]
                img = _call_main(lambda: _qt_screenshot(wid))
                self._send({"image": img})
            elif path == "/widgets":
                self._send({"widgets": _call_main(_qt_widget_tree)})
            elif path.startswith("/widget/"):
                wid = path.split("/")[2]
                self._send(_call_main(lambda: _qt_widget_info(wid)))
            elif path == "/logs":
                n = int(qs.get("n", ["50"])[0])
                self._send({"logs": _log_records[-n:]})
            elif path == "/app":
                self._send(_call_main(_qt_app_info))
            else:
                self._send({"error": "not found"}, 404)
        except Exception as exc:
            self._send({"error": str(exc)}, 500)

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        body = self._body()
        try:
            parts = path.split("/")
            if len(parts) >= 4 and parts[1] == "widget" and parts[3] == "click":
                wid = parts[2]
                self._send(_call_main(lambda: _qt_click_widget(wid, body)))
            elif len(parts) >= 4 and parts[1] == "widget" and parts[3] == "type":
                wid = parts[2]
                self._send(_call_main(lambda: _qt_type(wid, body)))
            elif path == "/click":
                self._send(_call_main(lambda: _qt_click_coord(body)))
            elif path == "/key":
                self._send(_call_main(lambda: _qt_press_key(body)))
            elif path == "/scroll":
                self._send(_call_main(lambda: _qt_scroll(body)))
            elif path == "/find":
                self._send(_call_main(lambda: _qt_find(body)))
            elif path == "/eval":
                self._send(_call_main(lambda: _qt_eval(body)))
            elif path == "/quit":
                self._send(_call_main(_qt_quit))
            else:
                self._send({"error": "not found"}, 404)
        except Exception as exc:
            self._send({"error": str(exc)}, 500)


class _ThreadedHTTP(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ---------------------------------------------------------------------------
# Qt operations (always called via _call_main → runs in main thread)
# ---------------------------------------------------------------------------

def _qt_screenshot(widget_id: str | None = None) -> str:
    if widget_id:
        w = _resolve(widget_id)
        if not w:
            raise ValueError(f"Widget {widget_id!r} not found")
        pixmap = w.grab()
    else:
        app = QApplication.instance()
        tops = [w for w in app.topLevelWidgets() if w.isVisible()]
        pixmap = tops[0].grab() if tops else app.primaryScreen().grabWindow(0)

    buf = QBuffer()
    buf.open(QIODevice.OpenMode.ReadWrite)
    pixmap.save(buf, "PNG")
    return base64.b64encode(bytes(buf.data())).decode()


def _qt_widget_tree() -> list:
    app = QApplication.instance()
    return [_widget_dict(w) for w in app.topLevelWidgets() if w.isVisible()]


def _qt_widget_info(wid: str) -> dict:
    w = _resolve(wid)
    if not w:
        return {"error": f"Widget {wid!r} not found"}
    d = _widget_dict(w, max_depth=1)
    d["dynamic_properties"] = {
        bytes(k).decode(): str(w.property(k))
        for k in w.dynamicPropertyNames()
    }
    return d


def _qt_click_widget(wid: str, body: dict) -> dict:
    from PySide6.QtTest import QTest

    w = _resolve(wid)
    if not w:
        return {"error": f"Widget {wid!r} not found"}
    btn_map = {
        "left": Qt.MouseButton.LeftButton,
        "right": Qt.MouseButton.RightButton,
        "middle": Qt.MouseButton.MiddleButton,
    }
    btn = btn_map.get(body.get("button", "left"), Qt.MouseButton.LeftButton)
    if "x" in body and "y" in body:
        QTest.mouseClick(w, btn, Qt.KeyboardModifier.NoModifier, QPoint(body["x"], body["y"]))
    else:
        QTest.mouseClick(w, btn)
    return {"ok": True}


def _qt_type(wid: str, body: dict) -> dict:
    from PySide6.QtTest import QTest

    w = _resolve(wid)
    if not w:
        return {"error": f"Widget {wid!r} not found"}
    w.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.keyClicks(w, body.get("text", ""))
    return {"ok": True}


def _qt_click_coord(body: dict) -> dict:
    from PySide6.QtTest import QTest

    app = QApplication.instance()
    top = next((w for w in app.topLevelWidgets() if w.isVisible()), None)
    if not top:
        return {"error": "No visible top-level window"}
    btn_map = {
        "left": Qt.MouseButton.LeftButton,
        "right": Qt.MouseButton.RightButton,
        "middle": Qt.MouseButton.MiddleButton,
    }
    btn = btn_map.get(body.get("button", "left"), Qt.MouseButton.LeftButton)
    QTest.mouseClick(top, btn, Qt.KeyboardModifier.NoModifier, QPoint(body["x"], body["y"]))
    return {"ok": True}


_KEY_MAP: dict[str, Qt.Key] = {
    "enter": Qt.Key.Key_Return,
    "return": Qt.Key.Key_Return,
    "escape": Qt.Key.Key_Escape,
    "tab": Qt.Key.Key_Tab,
    "backtab": Qt.Key.Key_Backtab,
    "backspace": Qt.Key.Key_Backspace,
    "delete": Qt.Key.Key_Delete,
    "up": Qt.Key.Key_Up,
    "down": Qt.Key.Key_Down,
    "left": Qt.Key.Key_Left,
    "right": Qt.Key.Key_Right,
    "space": Qt.Key.Key_Space,
    "home": Qt.Key.Key_Home,
    "end": Qt.Key.Key_End,
    "pageup": Qt.Key.Key_PageUp,
    "pagedown": Qt.Key.Key_PageDown,
    "f1": Qt.Key.Key_F1, "f2": Qt.Key.Key_F2, "f3": Qt.Key.Key_F3,
    "f4": Qt.Key.Key_F4, "f5": Qt.Key.Key_F5, "f6": Qt.Key.Key_F6,
}


def _qt_press_key(body: dict) -> dict:
    from PySide6.QtTest import QTest

    app = QApplication.instance()
    widget = app.focusWidget() or next(
        (w for w in app.topLevelWidgets() if w.isVisible()), None
    )
    if not widget:
        return {"error": "No focus widget"}

    key_str = body.get("key", "").lower()
    if key_str in _KEY_MAP:
        QTest.keyClick(widget, _KEY_MAP[key_str])
    else:
        QTest.keyClicks(widget, body.get("key", ""))
    return {"ok": True}


def _qt_scroll(body: dict) -> dict:
    wid = body.get("widget_id")
    w = _resolve(wid) if wid else None
    if w is None:
        app = QApplication.instance()
        w = next((ww for ww in app.topLevelWidgets() if ww.isVisible()), None)
    if not w:
        return {"error": "No target widget"}

    dx = int(body.get("dx", 0))
    dy = int(body.get("dy", 0))
    pos = QPoint(w.width() // 2, w.height() // 2)
    global_pos = w.mapToGlobal(pos)
    event = QWheelEvent(
        QPointF(pos), QPointF(global_pos),
        QPoint(dx, dy),
        QPoint(dx * 120, dy * 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(w, event)
    return {"ok": True}


def _qt_find(body: dict) -> dict:
    app = QApplication.instance()
    results = []
    for w in app.allWidgets():
        if "class" in body and type(w).__name__ != body["class"]:
            continue
        if "object_name" in body and w.objectName() != body["object_name"]:
            continue
        if "text" in body:
            t = _widget_text(w) or ""
            if body["text"].lower() not in t.lower():
                continue
        if "visible" in body and w.isVisible() != body["visible"]:
            continue
        results.append(_widget_dict(w, max_depth=0))
    return {"widgets": results, "count": len(results)}


def _qt_app_info() -> dict:
    app = QApplication.instance()
    aw = app.activeWindow()
    fw = app.focusWidget()
    return {
        "app_name": app.applicationName(),
        "version": app.applicationVersion(),
        "active_window": _widget_id(aw) if aw else None,
        "focus_widget": _widget_id(fw) if fw else None,
        "top_level_windows": [
            {"id": _widget_id(w), "class": type(w).__name__, "title": w.windowTitle()}
            for w in app.topLevelWidgets()
            if w.isVisible()
        ],
        "screens": [
            {"name": s.name(), "geometry": s.geometry().getRect()}
            for s in app.screens()
        ],
    }


def _qt_quit() -> dict:
    app = QApplication.instance()
    if not app:
        return {"error": "No QApplication"}
    app.quit()
    return {"ok": True}


def _qt_eval(body: dict) -> dict:
    code = body.get("code", "")
    app = QApplication.instance()
    ctx = {
        "app": app,
        "QApplication": QApplication,
        "widgets": {wid: ref() for wid, ref in _widget_registry.items() if ref()},
    }
    try:
        result = eval(code, ctx)  # noqa: S307
        return {"result": repr(result)}
    except SyntaxError:
        exec(code, ctx)  # noqa: S102
        return {"result": "executed"}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def install_bridge(port: int = 7890) -> None:
    """
    Start the pyside6-mcp bridge inside your PySide6 app.
    Call this once before app.exec():

        from pyside6_mcp import install_bridge
        install_bridge()
    """
    global _proxy
    # Must be created here (main thread) so Qt gives it main-thread affinity.
    _proxy = _MainProxy()
    logging.root.addHandler(_LogCapture())
    server = _ThreadedHTTP(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info("pyside6-mcp bridge started on port %d", port)
    # Never write to stdout: when the app is spawned by the MCP server's
    # launch_app tool it inherits the server's stdout, which is the JSON-RPC
    # channel. Anything printed there corrupts the protocol.
    print(f"[pyside6-mcp] Bridge listening on http://127.0.0.1:{port}", file=sys.stderr)
