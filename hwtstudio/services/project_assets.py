from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

from ..models import ThemeProject
from ..paths import ensure_no_symlink_parents, unique_temp_path

PROJECT_SUFFIX = ".hwtproj.json"


def project_assets_dir(path: Path) -> Path:
    path = Path(path)
    name = path.name
    base = name[: -len(PROJECT_SUFFIX)] if name.lower().endswith(PROJECT_SUFFIX) else path.stem
    return path.parent / f"{base}.assets"


def _safe_component(value: str, fallback: str) -> str:
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip(" ._")
    return (value or fallback)[:64]


def asset_name(slot_id: str, original_name: str) -> str:
    parts = slot_id.split("::")
    module = _safe_component(parts[0] if parts else "resource", "resource")
    resource = _safe_component(parts[-1] if parts else "image", "image")
    basename = _safe_component(Path(original_name).name, "image.png")
    digest = hashlib.sha256(slot_id.encode("utf-8")).hexdigest()[:8]
    prefix = f"{module}__{resource}__{digest}__"
    while basename.startswith(prefix):
        basename = basename[len(prefix):]
    return f"{prefix}{basename}"


def resolve_source(source: str, project_file: Path | None) -> Path:
    path = Path(source)
    if not path.is_absolute() and project_file is not None:
        path = project_file.parent / path
    return path.resolve()


def ensure_no_symlinks(root: Path) -> None:
    root = Path(root)
    if root.is_symlink():
        raise ValueError("工程资产目录不能是符号链接")
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in (*directories, *files):
            if (Path(directory) / name).is_symlink():
                raise ValueError("工程资产目录不能包含符号链接")


def _ensure_asset_directory(path: Path, message: str) -> None:
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError(message)
    ensure_no_symlink_parents(path / ".hwtstudio-path-check", message)


def _ensure_asset_file(path: Path, message: str) -> None:
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(message)
    ensure_no_symlink_parents(path, message)


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    stat = Path(path).stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _ensure_file_unchanged(path: Path, expected: tuple[int, int, int, int]) -> None:
    try:
        current = _file_signature(path)
    except OSError as exc:
        raise OSError(f"工程图片在复制时不可用：{path}") from exc
    if current != expected:
        raise OSError(f"工程图片在复制时发生变化：{path}")


def missing_project_assets(project: ThemeProject) -> list[tuple[str, Path]]:
    missing: list[tuple[str, Path]] = []
    for slot_id, change in project.changes.items():
        if not change.enabled or change.source_kind != "file" or not change.source_file:
            continue
        source = resolve_source(change.source_file, project.project_file)
        if not source.is_file():
            missing.append((slot_id, source))
    return missing


def collect_project_assets(
    project: ThemeProject,
    path: Path,
    serialized: dict,
    *,
    staging_dir: Path | None = None,
    include_disabled: bool = False,
) -> dict[str, Path]:
    asset_dir = project_assets_dir(path)
    copy_dir = Path(staging_dir) if staging_dir is not None else asset_dir
    _ensure_asset_directory(asset_dir, "工程资产目录不是普通目录")
    _ensure_asset_directory(copy_dir, "工程资产暂存目录不是普通目录")
    collected: dict[str, Path] = {}
    for slot_id, change in project.changes.items():
        if (not include_disabled and not change.enabled) or change.source_kind != "file" or not change.source_file:
            continue
        source = resolve_source(change.source_file, project.project_file)
        if not source.is_file():
            if not change.enabled:
                continue
            raise FileNotFoundError(f"工程图片不存在：{source}")
        copy_dir.mkdir(parents=True, exist_ok=True)
        _ensure_asset_directory(copy_dir, "工程资产暂存目录不是普通目录")
        target = copy_dir / asset_name(slot_id, source.name)
        _ensure_asset_file(target, "工程资产目标不是普通文件")
        source_signature = _file_signature(source)
        try:
            same_file = target.exists() and os.path.samefile(source, target)
        except OSError:
            same_file = source == target.resolve()
        if not same_file:
            copy_temp = unique_temp_path(target)
            _ensure_asset_file(copy_temp, "工程资产临时文件不是普通文件")
            try:
                shutil.copy2(source, copy_temp)
                _ensure_asset_file(copy_temp, "工程资产临时文件不是普通文件")
                _ensure_file_unchanged(source, source_signature)
                _ensure_asset_file(target, "工程资产目标不是普通文件")
                os.replace(copy_temp, target)
            finally:
                if not copy_temp.is_symlink() and (not copy_temp.exists() or copy_temp.is_file()):
                    copy_temp.unlink(missing_ok=True)
        final_target = asset_dir / target.name
        serialized["changes"][slot_id]["source_file"] = final_target.relative_to(path.parent).as_posix()
        collected[slot_id] = final_target.resolve()
    return collected
