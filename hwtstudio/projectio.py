from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .common import MAX_PROJECT_BYTES
from .models import ThemeProject
from .paths import unique_temp_path
from .services.project_assets import collect_project_assets, missing_project_assets, project_assets_dir, resolve_source

__all__ = ["load_project", "missing_project_assets", "project_assets_dir", "save_project"]

_PROJECT_TEXT_FIELDS = ("name", "title", "author", "designer", "version", "screen")
_CHANGE_TEXT_FIELDS = ("source_kind", "fit", "enhance")
_CUSTOM_RESOURCE_FIELDS = ("id", "module", "container", "resource_type", "name", "path", "category", "label")


def _remove_path(path: Path) -> None:
    path = Path(path)
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass


def _cleanup_path(path: Path) -> None:
    try:
        _remove_path(path)
    except OSError:
        pass


def _validate_project_payload(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("工程文件顶层必须是 JSON 对象")
    schema = raw.get("schema", 1)
    if isinstance(schema, bool) or not isinstance(schema, int) or schema not in {1, 2}:
        raise ValueError(f"不支持的工程格式版本：{schema}")
    for field in _PROJECT_TEXT_FIELDS:
        if field in raw and not isinstance(raw[field], str):
            raise ValueError(f"工程字段 {field} 必须是文字")
    changes = raw.get("changes", {})
    if not isinstance(changes, dict):
        raise ValueError("工程字段 changes 必须是 JSON 对象")
    for slot_id, change in changes.items():
        if not isinstance(slot_id, str) or not isinstance(change, dict):
            raise ValueError("工程修改记录格式无效")
        if change.get("slot_id") != slot_id:
            raise ValueError(f"工程修改记录 {slot_id} 的 slot_id 不匹配")
        if "enabled" in change and not isinstance(change["enabled"], bool):
            raise ValueError(f"工程修改记录 {slot_id} 的 enabled 必须是布尔值")
        for field in _CHANGE_TEXT_FIELDS:
            if field in change and not isinstance(change[field], str):
                raise ValueError(f"工程修改记录 {slot_id} 的 {field} 必须是文字")
        for field in ("value", "source_file"):
            if field in change and change[field] is not None and not isinstance(change[field], str):
                raise ValueError(f"工程修改记录 {slot_id} 的 {field} 必须是文字或空值")
        for field in ("focus_x", "focus_y", "enhance_strength"):
            if field in change and (
                isinstance(change[field], bool) or not isinstance(change[field], (int, float))
            ):
                raise ValueError(f"工程修改记录 {slot_id} 的 {field} 必须是数字")
    custom_resources = raw.get("custom_resources", [])
    if not isinstance(custom_resources, list) or any(not isinstance(item, dict) for item in custom_resources):
        raise ValueError("工程字段 custom_resources 必须是对象列表")
    for resource in custom_resources:
        if any(field not in resource for field in _CUSTOM_RESOURCE_FIELDS):
            raise ValueError("工程自定义资源缺少必需字段")
    return raw


def save_project(project: ThemeProject, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = project.to_dict()
    asset_dir = project_assets_dir(path)
    asset_stage = unique_temp_path(asset_dir, suffix=".tmp")
    temp = unique_temp_path(path)
    asset_backup = unique_temp_path(asset_dir, suffix=".backup")
    project_backup = unique_temp_path(path, suffix=".backup")
    collected: dict[str, Path] = {}
    has_file_assets = any(change.source_kind == "file" and change.source_file for change in project.changes.values())
    asset_backed_up = False
    asset_committed = False
    project_backed_up = False
    project_committed = False
    try:
        _cleanup_path(asset_stage)
        _cleanup_path(temp)
        _cleanup_path(asset_backup)
        _cleanup_path(project_backup)
        if (has_file_assets or asset_dir.exists()) and asset_dir.is_dir() and not asset_dir.is_symlink():
            shutil.copytree(asset_dir, asset_stage)
        collected = collect_project_assets(
            project,
            path,
            serialized,
            staging_dir=asset_stage if has_file_assets else None,
            include_disabled=True,
        )
        desired_assets = {target.name for target in collected.values()}
        if asset_stage.is_dir():
            for entry in asset_stage.iterdir():
                if entry.name not in desired_assets:
                    _remove_path(entry)
        if not desired_assets:
            _cleanup_path(asset_stage)
        encoded = json.dumps(serialized, ensure_ascii=False, indent=2).encode("utf-8")
        if len(encoded) > MAX_PROJECT_BYTES:
            raise ValueError("保存的工程文件超过允许的大小限制")
        temp.write_bytes(encoded)
        asset_changed = asset_dir.exists() or asset_dir.is_symlink() or asset_stage.exists()
        if asset_changed:
            if asset_dir.exists() or asset_dir.is_symlink():
                os.replace(asset_dir, asset_backup)
                asset_backed_up = True
            if desired_assets:
                os.replace(asset_stage, asset_dir)
            asset_committed = True
        if path.exists():
            os.replace(path, project_backup)
            project_backed_up = True
        os.replace(temp, path)
        project_committed = True
        for slot_id, target in collected.items():
            project.changes[slot_id].source_file = str(target)
        project.project_file = path
        project.dirty = False
    except Exception:
        rollback_errors: list[Exception] = []
        try:
            if project_committed:
                _remove_path(path)
            if project_backed_up and project_backup.exists():
                os.replace(project_backup, path)
        except Exception as rollback_exc:
            rollback_errors.append(rollback_exc)
        try:
            if asset_committed:
                _remove_path(asset_dir)
            if asset_backed_up and asset_backup.exists():
                os.replace(asset_backup, asset_dir)
        except Exception as rollback_exc:
            rollback_errors.append(rollback_exc)
        if rollback_errors:
            raise OSError("工程保存失败，且无法完整恢复旧工程或资产，请检查文件占用后重试") from rollback_errors[0]
        raise
    finally:
        _cleanup_path(temp)
        _cleanup_path(asset_stage)
        if project_committed:
            _cleanup_path(project_backup)
        if asset_committed:
            _cleanup_path(asset_backup)
    return path


def load_project(path: Path) -> ThemeProject:
    path = Path(path)
    with path.open("rb") as stream:
        encoded = stream.read(MAX_PROJECT_BYTES + 1)
    if len(encoded) > MAX_PROJECT_BYTES:
        raise ValueError("工程文件超过允许的大小限制")
    try:
        raw = _validate_project_payload(json.loads(encoded.decode("utf-8")))
    except UnicodeDecodeError as exc:
        raise ValueError("工程文件不是有效的 UTF-8 文本") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("工程文件不是有效的 JSON") from exc
    project = ThemeProject.from_dict(raw, project_file=path)
    for change in project.changes.values():
        if change.source_kind == "file" and change.source_file:
            change.source_file = str(resolve_source(change.source_file, path))
    return project
