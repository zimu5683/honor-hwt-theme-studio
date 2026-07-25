from __future__ import annotations

import json
import os
from pathlib import Path

from .models import ThemeProject
from .services.project_assets import collect_project_assets, missing_project_assets, project_assets_dir, resolve_source

__all__ = ["load_project", "missing_project_assets", "project_assets_dir", "save_project"]


def save_project(project: ThemeProject, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = project.to_dict()
    collected = collect_project_assets(project, path, serialized)

    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    for slot_id, target in collected.items():
        project.changes[slot_id].source_file = str(target)
    project.project_file = path
    project.dirty = False
    return path


def load_project(path: Path) -> ThemeProject:
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    schema = int(raw.get("schema", 1))
    if schema not in {1, 2}:
        raise ValueError(f"不支持的工程格式版本：{schema}")
    project = ThemeProject.from_dict(raw, project_file=path)
    for change in project.changes.values():
        if change.source_kind == "file" and change.source_file:
            change.source_file = str(resolve_source(change.source_file, path))
    return project
