from pyside6_mcp.bridge import _ready_from, _window_is_ready


def test_window_is_ready_true_for_visible_sized_window():
    assert _window_is_ready({"visible": True, "w": 800, "h": 600}) is True


def test_window_is_ready_false_for_zero_size():
    assert _window_is_ready({"visible": True, "w": 0, "h": 600}) is False


def test_window_is_ready_false_for_hidden():
    assert _window_is_ready({"visible": False, "w": 800, "h": 600}) is False


def test_ready_from_requires_visible_window_and_quiet():
    assert _ready_from(has_visible_window=True, idle_ms=600, quiet_ms=500) is True


def test_ready_from_false_when_not_idle_long_enough():
    assert _ready_from(has_visible_window=True, idle_ms=200, quiet_ms=500) is False


def test_ready_from_false_when_no_window():
    assert _ready_from(has_visible_window=False, idle_ms=5000, quiet_ms=500) is False
