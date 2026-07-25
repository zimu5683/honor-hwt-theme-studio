from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "大雪主题编辑器"


def bundle_root() -> Path:
    """Return the source tree or PyInstaller extraction root."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path.home() / ".local" / "share"
    path = root / "HwtThemeStudio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def bundled_catalog() -> Path:
    return bundle_root() / "assets" / "catalog_daxue.json"


def bundled_blank_theme() -> Path:
    return bundle_root() / "assets" / "空白主题_子木.hwt"


def default_source_theme() -> Path:
    return Path(r"D:\HONOR Share\Honor Share\30039574_大雪.hwt")

