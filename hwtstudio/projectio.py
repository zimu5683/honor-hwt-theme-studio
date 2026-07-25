from __future__ import annotations

import json
from pathlib import Path

from .models import ThemeProject


def save_project(project: ThemeProject, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    project.project_file = path
    project.dirty = False
    return path


def load_project(path: Path) -> ThemeProject:
    path = Path(path)
    return ThemeProject.from_dict(json.loads(path.read_text(encoding="utf-8")), project_file=path)

