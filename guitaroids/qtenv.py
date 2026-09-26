"""Qt environment bootstrap.

**MUST be imported before PySide6 or cv2 anywhere in the process.**

DESIGN.md §2.2: mediapipe hard-requires the GUI build of OpenCV, whose bundled
Qt plugins under ``cv2/qt/plugins`` hijack ``QT_PLUGIN_PATH`` and break PySide6
with ``Could not load the Qt platform plugin "xcb"``.

The primary fix is to install ``opencv-contrib-python-headless`` at the identical
version so ``cv2/qt`` does not exist at all. This module is the second line of
defence: it points Qt explicitly at PySide6's own plugins so that even if a GUI
OpenCV build reappears, it cannot win the plugin search.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

_applied = False


def pyside6_root() -> Path | None:
    """Locate the installed PySide6 package without importing it."""
    spec = importlib.util.find_spec("PySide6")
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).parent


def plugin_dir() -> Path | None:
    root = pyside6_root()
    if root is None:
        return None
    candidate = root / "Qt" / "plugins"
    return candidate if candidate.is_dir() else None


def _prepend_env(name: str, value: str) -> None:
    existing = os.environ.get(name, "")
    parts = [p for p in existing.split(os.pathsep) if p]
    if value not in parts:
        parts.insert(0, value)
    os.environ[name] = os.pathsep.join(parts)


def apply() -> Path | None:
    """Point Qt at PySide6's bundled plugins. Idempotent."""
    global _applied
    if _applied:
        return plugin_dir()

    plugins = plugin_dir()
    if plugins is not None:
        _prepend_env("QT_PLUGIN_PATH", str(plugins))
        platforms = plugins / "platforms"
        if platforms.is_dir():
            _prepend_env("QT_QPA_PLATFORM_PLUGIN_PATH", str(platforms))
    _applied = True
    return plugins


def describe() -> str:
    """One-line diagnostic, surfaced in the M0 gate and the debug HUD."""
    plugins = plugin_dir()
    return (
        f"PySide6={pyside6_root()} "
        f"plugins={plugins} "
        f"QT_PLUGIN_PATH={os.environ.get('QT_PLUGIN_PATH', '<unset>')}"
    )
