"""
Launcher: run any PySide6 app with the bridge pre-installed.

Usage:
    python -m pyside6_mcp myapp.py [args...]
    python -m pyside6_mcp -m mypackage.module [args...]
    uv run python -m pyside6_mcp main.py

The bridge is injected by monkey-patching QApplication.__init__ before
the target script runs. The target app requires zero modifications.
"""
import os
import runpy
import sys


def _patch_qapp() -> None:
    from PySide6.QtWidgets import QApplication
    from pyside6_mcp.bridge import install_bridge

    port = int(os.environ.get("PYSIDE6_MCP_PORT", "7890"))
    _orig = QApplication.__init__
    _done = []

    def _patched(self, *args, **kwargs):
        _orig(self, *args, **kwargs)
        if not _done:
            _done.append(True)
            install_bridge(port=port)

    QApplication.__init__ = _patched


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: python -m pyside6_mcp <script.py> [args...]")
        print("       python -m pyside6_mcp -m <module> [args...]")
        sys.exit(1)

    _patch_qapp()

    if args[0] == "-m":
        if len(args) < 2:
            print("Error: -m requires a module name")
            sys.exit(1)
        sys.argv = args[1:]
        runpy.run_module(args[1], run_name="__main__", alter_sys=True)
    else:
        sys.argv = args
        runpy.run_path(args[0], run_name="__main__")


if __name__ == "__main__":
    main()
