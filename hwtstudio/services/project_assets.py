from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

from ..models import ThemeProject


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


def missing_project_assets(project: ThemeProject) -> list[tuple[str, Path]]:
    missing: list[tuple[str, Path]] = []
    for slot_id, change in project.changes.items():
        if not change.enabled or change.source_kind != "file" or not change.source_file:
            continue
        source = resolve_source(change.source_file, project.project_file)
        if not source.is_file():
            missing.append((slot_id, source))
    return missing


def collect_project_assets(project: ThemeProject, path: Path, serialized: dict) -> dict[str, Path]:
    asset_dir = project_assets_dir(path)
    collected: dict[str, Path] = {}
    for slot_id, change in project.changes.items():
        if not change.enabled or change.source_kind != "file" or not change.source_file:
            continue
        source = resolve_source(change.source_file, project.project_file)
        if not source.is_file():
            raise FileNotFoundError(f"工程图片不存在：{source}")
        asset_dir.mkdir(parents=True, exist_ok=True)
        target = asset_dir / asset_name(slot_id, source.name)
        try:
            same_file = target.exists() and os.path.samefile(source, target)
        except OSError:
            same_file = source == target.resolve()
        if not same_file:
            copy_temp = target.with_suffix(target.suffix + ".tmp")
            try:
                shutil.copy2(source, copy_temp)
                os.replace(copy_temp, target)
            finally:
                copy_temp.unlink(missing_ok=True)
        serialized["changes"][slot_id]["source_file"] = target.relative_to(path.parent).as_posix()
        collected[slot_id] = target.resolve()
    return collected
