from __future__ import annotations

import os
import sys
import threading
from pathlib import Path


APP_NAME = "大雪主题编辑器"


def bundle_root() -> Path:
    """Return the source tree or PyInstaller extraction root."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    if os.name == "nt":
        configured_root = os.environ.get("LOCALAPPDATA", "").strip()
        root = Path(configured_root) if configured_root else Path.home() / "AppData" / "Local"
    else:
        root = Path.home() / ".local" / "share"
    if not root.is_absolute():
        raise OSError("应用数据根目录必须是绝对路径")
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise OSError("应用数据根目录不是普通目录")
    path = root / "HwtThemeStudio"
    try:
        ensure_no_symlink_parents(path, "应用数据目录的父路径不能包含符号链接")
    except ValueError as exc:
        raise OSError(str(exc)) from exc
    if path.is_symlink():
        raise OSError("应用数据目录不能是符号链接")
    if path.exists() and not path.is_dir():
        raise OSError("应用数据目录不是目录")
    path.mkdir(parents=True, exist_ok=True)
    try:
        ensure_no_symlink_parents(path, "应用数据目录的父路径不能包含符号链接")
    except ValueError as exc:
        raise OSError(str(exc)) from exc
    if path.is_symlink() or not path.is_dir():
        raise OSError("应用数据目录不是普通目录")
    return path


def unique_temp_path(path: Path, suffix: str = ".tmp") -> Path:
    path = Path(path)
    return path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}{suffix}")


def ensure_no_symlink_parents(path: Path, message: str) -> None:
    """Reject output paths whose existing parent components include symlinks."""
    parent = Path(path).parent
    if not parent.is_absolute():
        parent = Path.cwd() / parent
    current = Path(parent.anchor) if parent.anchor else Path()
    for component in parent.parts:
        if component == parent.anchor:
            continue
        current /= component
        if current.is_symlink():
            raise ValueError(message)


def bundled_catalog() -> Path:
    return bundle_root() / "assets" / "catalog_daxue.json"


def bundled_blank_theme() -> Path:
    return bundle_root() / "assets" / "空白主题_子木.hwt"


def default_source_theme() -> Path:
    return Path(r"D:\HONOR Share\Honor Share\30039574_大雪.hwt")
