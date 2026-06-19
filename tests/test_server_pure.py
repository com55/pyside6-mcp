import httpx

from pyside6_mcp.server import _app_status_from, _should_retry


def test_should_retry_connect_error():
    assert _should_retry(httpx.ConnectError("boom")) is True


def test_should_retry_read_error():
    assert _should_retry(httpx.ReadError("boom")) is True


def test_should_retry_remote_protocol_error():
    assert _should_retry(httpx.RemoteProtocolError("boom")) is True


def test_should_not_retry_http_status_error():
    request = httpx.Request("GET", "http://127.0.0.1/x")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("err", request=request, response=response)
    assert _should_retry(exc) is False


def test_status_running_and_responsive():
    s = _app_status_from(proc_alive=True, exit_code=None, bridge_ok=True, timed_out=False)
    assert s["running"] is True
    assert s["bridge_responsive"] is True
    assert s["likely_modal_block"] is False


def test_status_alive_but_bridge_timed_out_means_modal_block():
    s = _app_status_from(proc_alive=True, exit_code=None, bridge_ok=False, timed_out=True)
    assert s["running"] is True
    assert s["bridge_responsive"] is False
    assert s["likely_modal_block"] is True


def test_status_exited():
    s = _app_status_from(proc_alive=False, exit_code=1, bridge_ok=False, timed_out=False)
    assert s["running"] is False
    assert s["exit_code"] == 1
    assert s["likely_modal_block"] is False
