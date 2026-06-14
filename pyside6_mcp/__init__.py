__all__ = ["install_bridge"]


def __getattr__(name: str):
    if name == "install_bridge":
        from .bridge import install_bridge

        return install_bridge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
