"""Background service daemon for Syke."""

from syke.daemon.daemon import install_and_start, stop_and_unload

__all__ = ["install_and_start", "stop_and_unload"]
