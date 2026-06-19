import json
import os
import subprocess
import sys
import time

import httpx
import pytest

EXAMPLE = "examples/test_app.py"


def _os_environ():
    return os.environ.copy()


@pytest.mark.integration
def test_ready_endpoint_reports_ready_after_window_shown(free_port):
    env_port = str(free_port)
    proc = subprocess.Popen(
        [sys.executable, "-m", "pyside6_mcp", EXAMPLE],
        env={**_os_environ(), "PYSIDE6_MCP_PORT": env_port, "QT_QPA_PLATFORM": "offscreen"},
    )
    base = f"http://127.0.0.1:{free_port}"
    try:
        ready = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"{base}/ready", timeout=1)
                if r.status_code == 200 and r.json().get("ready"):
                    ready = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        assert ready, "bridge never reported ready"
        body = httpx.get(f"{base}/ready", timeout=2).json()
        assert body["has_visible_window"] is True
        assert body["windows"], "expected at least one window"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.integration
def test_rapid_calls_reuse_connection(free_port):
    proc = subprocess.Popen(
        [sys.executable, "-m", "pyside6_mcp", EXAMPLE],
        env={
            **_os_environ(),
            "PYSIDE6_MCP_PORT": str(free_port),
            "QT_QPA_PLATFORM": "offscreen",
        },
    )
    base = f"http://127.0.0.1:{free_port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base}/ready", timeout=1).json().get("ready"):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        with httpx.Client(base_url=base, timeout=5) as client:
            for _ in range(50):
                assert client.get("/app").status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _call_launch_app(server, command: str, port: int, timeout: int = 25) -> str:
    """Invoke launch_app regardless of FastMCP decorator wrapper."""
    fn = getattr(server.launch_app, "fn", server.launch_app)
    return fn(command=command, port=port, timeout=timeout)


def _call_stop_app(server, port: int) -> str:
    fn = getattr(server.stop_app, "fn", server.stop_app)
    return fn(port=port)


@pytest.mark.integration
def test_launch_app_tool_waits_for_ready(free_port, monkeypatch):
    monkeypatch.setenv("PYSIDE6_MCP_PORT", str(free_port))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    import importlib

    import pyside6_mcp.server as server

    importlib.reload(server)

    result = _call_launch_app(
        server,
        command=f"{sys.executable} -m pyside6_mcp {EXAMPLE}",
        port=free_port,
        timeout=25,
    )
    data = json.loads(result)
    try:
        assert data.get("ok") is True, data
        assert data["ready"]["ready"] is True
    finally:
        _call_stop_app(server, port=free_port)


@pytest.mark.integration
def test_screenshot_returns_png_base64(free_port):
    import base64 as _b64
    import os

    proc = subprocess.Popen(
        [sys.executable, "-m", "pyside6_mcp", EXAMPLE],
        env={**os.environ.copy(), "PYSIDE6_MCP_PORT": str(free_port),
             "QT_QPA_PLATFORM": "offscreen"},
    )
    base = f"http://127.0.0.1:{free_port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base}/ready", timeout=1).json().get("ready"):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        img = httpx.get(f"{base}/screenshot", timeout=5).json()["image"]
        raw = _b64.b64decode(img)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.integration
def test_idle_endpoint_reports_idle_ms(free_port):
    import os

    proc = subprocess.Popen(
        [sys.executable, "-m", "pyside6_mcp", EXAMPLE],
        env={**os.environ.copy(), "PYSIDE6_MCP_PORT": str(free_port),
             "QT_QPA_PLATFORM": "offscreen"},
    )
    base = f"http://127.0.0.1:{free_port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base}/ready", timeout=1).json().get("ready"):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        time.sleep(0.5)
        idle = httpx.get(f"{base}/idle", timeout=2).json()["idle_ms"]
        assert idle >= 0.0
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.integration
def test_list_actions_returns_list(free_port):
    import os

    proc = subprocess.Popen(
        [sys.executable, "-m", "pyside6_mcp", EXAMPLE],
        env={**os.environ.copy(), "PYSIDE6_MCP_PORT": str(free_port),
             "QT_QPA_PLATFORM": "offscreen"},
    )
    base = f"http://127.0.0.1:{free_port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base}/ready", timeout=1).json().get("ready"):
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        body = httpx.get(f"{base}/actions", timeout=3).json()
        assert "actions" in body
        assert isinstance(body["actions"], list)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
