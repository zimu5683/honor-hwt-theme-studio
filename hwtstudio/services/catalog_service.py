from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..catalog import (
    load_catalog,
    save_catalog,
    save_source_compatibility_report,
    scan_theme,
)
from ..common import MAX_CATALOG_BYTES
from ..locking import InterprocessLockTimeoutError, interprocess_lock
from ..models import ThemeCatalog
from ..paths import bundled_catalog, data_dir, default_source_theme, unique_temp_path


_CATALOG_FILE_NAME = "catalog_daxue.json"
_REPORT_FILE_NAME = "source_compatibility.report.json"
_TRANSACTION_FILE_NAME = ".catalog_bundle.transaction.json"
_TRANSACTION_SCHEMA = 1
_MAX_TRANSACTION_BYTES = 16 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CATALOG_LOCK_TIMEOUT = 5.0
_CATALOG_LOCK_GUARD = threading.Lock()
_CATALOG_LOCKS: dict[Path, threading.RLock] = {}


@contextmanager
def _catalog_bundle_lock(root: Path) -> Iterator[None]:
    target = (root / _CATALOG_FILE_NAME).resolve()
    with _CATALOG_LOCK_GUARD:
        thread_lock = _CATALOG_LOCKS.setdefault(target, threading.RLock())
    with thread_lock:
        with interprocess_lock(
            target,
            timeout=_CATALOG_LOCK_TIMEOUT,
            timeout_message="用户资源目录锁等待超时",
        ):
            yield


def _bounded_sha256(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CATALOG_BYTES:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _catalog_source_is_stale(catalog: ThemeCatalog) -> bool:
    source_name = catalog.source_path
    if not source_name:
        return False
    source = Path(source_name)
    if not source.is_file():
        return False
    expected = catalog.source_sha256
    if not expected:
        return True
    actual = _bounded_sha256(source)
    return actual != expected.lower()


def _safe_unlink(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _transaction_path(root: Path) -> Path:
    return root / _TRANSACTION_FILE_NAME


def _stage_name_is_safe(name: object, target_name: str, suffix: str) -> bool:
    return (
        isinstance(name, str)
        and Path(name).name == name
        and name.startswith(f".{target_name}.")
        and name.endswith(suffix)
    )


def _transaction_entries(raw: object) -> list[dict] | None:
    if not isinstance(raw, dict):
        return None
    schema = raw.get("schema")
    files = raw.get("files")
    if isinstance(schema, bool) or schema != _TRANSACTION_SCHEMA or not isinstance(files, list):
        return None
    expected_targets = {_CATALOG_FILE_NAME, _REPORT_FILE_NAME}
    entries: list[dict] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            return None
        target = item.get("target")
        if not isinstance(target, str) or target not in expected_targets or target in seen:
            return None
        stage = item.get("stage")
        backup = item.get("backup")
        backup_hash = item.get("backup_sha256")
        if not _stage_name_is_safe(stage, target, ".pending"):
            return None
        if backup is not None and not _stage_name_is_safe(backup, target, ".backup"):
            return None
        if not isinstance(item.get("sha256"), str) or not _SHA256_PATTERN.fullmatch(item["sha256"]):
            return None
        original_exists = item.get("original_exists")
        if not isinstance(original_exists, bool):
            return None
        if original_exists:
            if backup is None or not isinstance(backup_hash, str) or not _SHA256_PATTERN.fullmatch(backup_hash):
                return None
        elif backup is not None or backup_hash is not None:
            return None
        entries.append({
            "target": target,
            "stage": stage,
            "backup": backup,
            "sha256": item["sha256"],
            "backup_sha256": backup_hash,
            "original_exists": original_exists,
        })
        seen.add(target)
    if seen != expected_targets:
        return None
    return entries


def _read_transaction(root: Path) -> list[dict] | None:
    marker = _transaction_path(root)
    try:
        if marker.is_symlink() or not marker.is_file():
            return None
        encoded = marker.read_bytes()
        if len(encoded) > _MAX_TRANSACTION_BYTES:
            return None
        raw = json.loads(encoded.decode("utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return _transaction_entries(raw)


def _write_transaction(root: Path, entries: list[dict]) -> None:
    marker = _transaction_path(root)
    temp = unique_temp_path(marker, suffix=".tmp")
    payload = {"schema": _TRANSACTION_SCHEMA, "files": entries}
    try:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with temp.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, marker)
    finally:
        _safe_unlink(temp)


def _cleanup_transaction(root: Path, entries: list[dict]) -> None:
    for item in entries:
        _safe_unlink(root / item["stage"])
        if item["backup"]:
            _safe_unlink(root / item["backup"])
    _safe_unlink(_transaction_path(root))


def _complete_transaction(root: Path, entries: list[dict]) -> bool:
    for item in entries:
        target = root / item["target"]
        stage = root / item["stage"]
        expected = item["sha256"]
        if target.is_symlink() or stage.is_symlink():
            return False
        if _bounded_sha256(target) == expected:
            continue
        if _bounded_sha256(stage) != expected:
            return False
        try:
            os.replace(stage, target)
        except OSError:
            return False
    if all(_bounded_sha256(root / item["target"]) == item["sha256"] for item in entries):
        _cleanup_transaction(root, entries)
        return True
    return False


def _rollback_transaction(root: Path, entries: list[dict]) -> bool:
    try:
        for item in entries:
            target = root / item["target"]
            backup = root / item["backup"] if item["backup"] else None
            if item["original_exists"]:
                if backup is None or _bounded_sha256(backup) != item["backup_sha256"]:
                    return False
                if target.is_symlink() or (target.exists() and not target.is_file()):
                    return False
                os.replace(backup, target)
            else:
                if target.is_symlink() or (target.exists() and not target.is_file()):
                    return False
                _safe_unlink(target)
        _cleanup_transaction(root, entries)
        return True
    except OSError:
        return False


def _recover_catalog_transaction(root: Path) -> tuple[bool, str]:
    marker = _transaction_path(root)
    if marker.is_symlink():
        if not _safe_unlink(marker):
            return False, "资源目录事务记录不是普通文件，且无法清理"
        return True, "资源目录事务记录不是普通文件，已清理"
    if not marker.exists():
        return True, ""
    if not marker.is_file():
        return False, "资源目录事务记录不是普通文件，无法恢复"
    entries = _read_transaction(root)
    if entries is None:
        if not _safe_unlink(marker):
            return False, "资源目录事务记录损坏，且无法清理"
        return True, "资源目录事务记录损坏，已清理"
    if _complete_transaction(root, entries):
        return True, "资源目录与兼容性报告已从未完成事务中恢复"
    if _rollback_transaction(root, entries):
        return True, "资源目录事务未完成，已回滚到上一版本"
    return False, "资源目录事务无法恢复，已暂时忽略用户缓存"


def _save_catalog_bundle(catalog: ThemeCatalog, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    targets = [root / _CATALOG_FILE_NAME, root / _REPORT_FILE_NAME]
    stages = [unique_temp_path(target, suffix=".pending") for target in targets]
    backups = [unique_temp_path(target, suffix=".backup") for target in targets]
    entries: list[dict] = []
    marker_written = False
    try:
        for artifact in (*stages, *backups):
            if artifact.is_symlink() or (artifact.exists() and not artifact.is_file()):
                reason = "符号链接" if artifact.is_symlink() else "普通文件"
                raise OSError(f"资源目录事务临时对象不是{reason}：{artifact}")
        save_catalog(catalog, stages[0])
        save_source_compatibility_report(catalog, stages[1])
        for target, stage, backup in zip(targets, stages, backups):
            if target.is_symlink() or (target.exists() and not target.is_file()):
                reason = "符号链接" if target.is_symlink() else "普通文件"
                raise OSError(f"资源目录目标不是{reason}：{target}")
            original_exists = target.is_file()
            backup_name = backup.name if original_exists else None
            backup_hash = None
            if original_exists:
                shutil.copyfile(target, backup)
                backup_hash = _bounded_sha256(backup)
                if backup_hash is None:
                    raise OSError(f"无法备份用户资源目录：{target}")
            new_hash = _bounded_sha256(stage)
            if new_hash is None:
                raise OSError(f"无法校验待保存的用户资源目录：{stage}")
            entries.append({
                "target": target.name,
                "stage": stage.name,
                "backup": backup_name,
                "sha256": new_hash,
                "backup_sha256": backup_hash,
                "original_exists": original_exists,
            })
        _write_transaction(root, entries)
        marker_written = True
        if not _complete_transaction(root, entries):
            raise OSError("资源目录与兼容性报告提交校验失败")
    except Exception:
        if marker_written and not _rollback_transaction(root, entries):
            raise OSError("资源目录保存失败，且无法回滚到上一版本")
        raise
    finally:
        for stage, backup in zip(stages, backups):
            _safe_unlink(stage)
            _safe_unlink(backup)


def _load_preferred_catalog_unlocked(root: Path) -> tuple[ThemeCatalog, str]:
    """Load a valid user scan first, falling back to bundled/source data."""
    recovered, warning = _recover_catalog_transaction(root)
    cached = root / _CATALOG_FILE_NAME
    if recovered and cached.is_file():
        try:
            catalog = load_catalog(cached)
            if not catalog.resources:
                raise ValueError("资源目录为空")
            if _catalog_source_is_stale(catalog):
                raise ValueError("缓存对应的源主题已变化，缓存已过期")
            return catalog, warning
        except Exception as exc:
            fallback_warning = f"用户扫描目录已过期或损坏，已回退到内置目录：{exc}"
            warning = f"{warning}\n{fallback_warning}" if warning else fallback_warning
    bundled = bundled_catalog()
    if bundled.is_file():
        return load_catalog(bundled), warning
    source = default_source_theme()
    if not source.is_file():
        raise FileNotFoundError("找不到资源目录，也找不到默认大雪主题。")
    catalog = scan_theme(source)
    if recovered:
        save_catalog(catalog, cached)
    return catalog, warning


def load_preferred_catalog() -> tuple[ThemeCatalog, str]:
    root = data_dir()
    try:
        with _catalog_bundle_lock(root):
            return _load_preferred_catalog_unlocked(root)
    except InterprocessLockTimeoutError:
        bundled = bundled_catalog()
        if bundled.is_file():
            return load_catalog(bundled), "用户资源目录正在被其他进程更新，已暂时使用内置目录"
        raise


def save_user_catalog(catalog: ThemeCatalog) -> None:
    root = data_dir()
    with _catalog_bundle_lock(root):
        recovered, warning = _recover_catalog_transaction(root)
        if not recovered:
            raise OSError(warning)
        _save_catalog_bundle(catalog, root)
