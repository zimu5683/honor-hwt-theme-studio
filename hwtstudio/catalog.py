from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from PIL import Image

from .archive_safety import (
    archive_path_overlaps,
    compression_ratio,
    duplicate_names,
    duplicate_normalized_names,
    is_symlink,
    zip64_inconsistencies,
)
from .common import (
    COMMON_BACKGROUND_TARGETS,
    MAX_ARCHIVE_COMPRESSION_RATIO,
    MAX_ARCHIVE_ENTRIES,
    MAX_ARCHIVE_ENTRY_BYTES,
    MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    MAX_CATALOG_BYTES,
    friendly_label,
    is_safe_archive_path,
    module_category,
    normalize_archive_path,
    risk_for,
)
from .models import ResourceSlot, ThemeCatalog
from .paths import ensure_no_symlink_parents, unique_temp_path
from .pngmeta import extract_android_chunks
from .xmlutil import parse_xml


VALUE_PATTERN = re.compile(
    rb"<(color|bool|integer|dimen|string)\s+[^>]*name\s*=\s*['\"]([^'\"]+)['\"][^>]*>(.*?)</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_FORMATS = {"PNG", "JPEG", "WEBP"}
_RESOURCE_STRING_FIELDS = ("id", "module", "container", "resource_type", "name", "path", "category", "label")
_RESOURCE_OPTIONAL_STRING_FIELDS = ("status", "risk", "mode", "actual_format", "extension")
_CATALOG_RESOURCE_TYPES = {"color", "bool", "integer", "dimen", "string", "image", "icon", "wallpaper", "preview"}
_CATALOG_TEXT_MAX_CHARS = 4096
_CATALOG_MODULE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def detect_format(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "WEBP"
    return "UNKNOWN"


def _expected_image_format(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix == ".png":
        return "PNG"
    if suffix in {".jpg", ".jpeg"}:
        return "JPEG"
    if suffix == ".webp":
        return "WEBP"
    return None


def _is_image_payload(path: str, raw: bytes) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS or detect_format(raw) in IMAGE_FORMATS


def _archive_blocked_paths(infos, warnings: list[dict], *, module: str | None = None) -> set[str]:
    blocked: set[str] = set()
    prefix = "nested_" if module is not None else ""

    for duplicate in duplicate_names(infos):
        blocked.update(info.filename for info in infos if info.filename == duplicate)
        item = {"kind": f"{prefix}duplicate_zip_entry", "path": duplicate}
        if module is not None:
            item["module"] = module
        warnings.append(item)
    for duplicate in duplicate_normalized_names(infos):
        blocked.update(
            info.filename
            for info in infos
            if normalize_archive_path(info.filename) == duplicate
        )
        item = {"kind": f"{prefix}duplicate_normalized_zip_entry", "path": duplicate}
        if module is not None:
            item["module"] = module
        warnings.append(item)
    for parent, path in archive_path_overlaps(infos):
        blocked.update(
            info.filename
            for info in infos
            if normalize_archive_path(info.filename.rstrip("/")) in {parent, path}
        )
        item = {"kind": f"{prefix}path_overlap", "path": path, "parent": parent}
        if module is not None:
            item["module"] = module
        warnings.append(item)

    for info in infos:
        ratio = compression_ratio(info)
        if ratio is not None and ratio > MAX_ARCHIVE_COMPRESSION_RATIO:
            blocked.add(info.filename)
            item = {
                "kind": f"{prefix}compression_ratio",
                "path": info.filename,
                "ratio": ratio,
                "limit": MAX_ARCHIVE_COMPRESSION_RATIO,
            }
            if module is not None:
                item["module"] = module
            warnings.append(item)
        if is_symlink(info):
            blocked.add(info.filename)
            item = {"kind": f"{prefix}symlink_entry", "path": info.filename}
            if module is not None:
                item["module"] = module
            warnings.append(item)
        for issue in zip64_inconsistencies(info):
            blocked.add(info.filename)
            item = {
                "kind": f"{prefix}zip64_inconsistent",
                "path": info.filename,
                "message": issue,
            }
            if module is not None:
                item["module"] = module
            warnings.append(item)
    return blocked


def _scan_xml(module: str, container: str, raw: bytes, warnings: list[dict]) -> list[ResourceSlot]:
    matches = list(VALUE_PATTERN.finditer(raw))
    grouped: dict[tuple[str, str], int] = Counter()
    for match in matches:
        grouped[(match.group(1).decode("ascii").lower(), match.group(2).decode("utf-8", "replace"))] += 1
    resources = []
    for (resource_type, name), occurrences in grouped.items():
        slot_id = f"{module}::{container}::{resource_type}::{name}"
        resources.append(
            ResourceSlot(
                id=slot_id,
                module=module,
                container=container,
                resource_type=resource_type,
                name=name,
                path=container,
                category=module_category(module),
                label=friendly_label(name, resource_type),
                risk=risk_for(module, name, resource_type),
                occurrences=occurrences,
            )
        )
        if occurrences > 1:
            warnings.append(
                {
                    "kind": "duplicate_resource",
                    "module": module,
                    "path": container,
                    "type": resource_type,
                    "name": name,
                    "occurrences": occurrences,
                }
            )
    # Record strict XML failures without losing values recoverable by the token scanner.
    try:
        parse_xml(raw)
    except Exception as exc:
        warnings.append({"kind": "nonstandard_xml", "module": module, "path": container, "message": str(exc)})
    return resources


def _image_slot(
    module: str,
    path: str,
    raw: bytes,
    resource_type: str = "image",
    warnings: list[dict] | None = None,
) -> ResourceSlot:
    width = height = None
    mode = None
    actual_format = detect_format(raw)
    try:
        with Image.open(BytesIO(raw)) as image:
            width, height, mode = image.width, image.height, image.mode
    except Exception:
        pass
    expected_format = _expected_image_format(path)
    if warnings is not None and expected_format is not None and actual_format != expected_format:
        warnings.append(
            {
                "kind": "image_format_mismatch",
                "module": module,
                "path": path,
                "expected": expected_format,
                "actual": actual_format,
            }
        )
    name = Path(path).name
    chunks = extract_android_chunks(raw) if actual_format == "PNG" else {}
    return ResourceSlot(
        id=f"{module}::image::{path}",
        module=module,
        container="",
        resource_type=resource_type,
        name=name,
        path=path,
        category=module_category(module),
        label=friendly_label(name, resource_type),
        risk=risk_for(module, name, resource_type),
        width=width,
        height=height,
        mode=mode,
        actual_format=actual_format,
        extension=Path(path).suffix.lower(),
        ninepatch="npTc" in chunks,
        png_chunks=chunks,
    )


def _scan_module(module: str, raw: bytes, warnings: list[dict]) -> tuple[list[ResourceSlot], dict[str, int]]:
    resources: list[ResourceSlot] = []
    stats = Counter()
    try:
        with ZipFile(BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise ValueError(f"主题模块条目数量超过 {MAX_ARCHIVE_ENTRIES} 条")
            if any(info.file_size > MAX_ARCHIVE_ENTRY_BYTES for info in infos if not info.is_dir()):
                raise ValueError(f"主题模块条目超过 {MAX_ARCHIVE_ENTRY_BYTES} 字节")
            if sum(info.file_size for info in infos if not info.is_dir()) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ValueError("主题模块解压总量超过限制")
            unsafe_paths = {
                info.filename for info in infos if not is_safe_archive_path(info.filename.rstrip("/"))
            }
            for path in sorted(unsafe_paths):
                warnings.append({"kind": "unsafe_nested_path", "module": module, "path": path})
            blocked_paths = set(unsafe_paths)
            blocked_paths.update(_archive_blocked_paths(infos, warnings, module=module))
            try:
                bad = None if blocked_paths else archive.testzip()
            except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
                warnings.append({"kind": "nested_crc_check", "module": module, "message": str(exc)})
                bad = None
            if bad:
                warnings.append({"kind": "module_crc", "module": module, "path": bad})
                blocked_paths.add(bad)
            for info in infos:
                if info.is_dir():
                    continue
                if info.filename in blocked_paths:
                    continue
                path = info.filename
                try:
                    data = archive.read(info)
                except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
                    warnings.append({"kind": "nested_entry_read", "module": module, "path": path, "message": str(exc)})
                    continue
                if path.endswith("theme.xml"):
                    slots = _scan_xml(module, path, data, warnings)
                    resources.extend(slots)
                    for slot in slots:
                        stats[f"{slot.resource_type}_slots"] += 1
                        stats[f"{slot.resource_type}_declarations"] += slot.occurrences
                elif _is_image_payload(path, data):
                    kind = "icon" if module == "icons" else "image"
                    resources.append(_image_slot(module, path, data, kind, warnings))
                    stats[f"{kind}_slots"] += 1
    except BadZipFile:
        warnings.append({"kind": "bad_module_zip", "module": module})
    except (OSError, RuntimeError, ValueError) as exc:
        warnings.append({"kind": "module_read", "module": module, "message": str(exc)})
    return resources, dict(stats)


def _synthetic_background_slots() -> list[ResourceSlot]:
    result = []
    for label, modules in COMMON_BACKGROUND_TARGETS.items():
        targets = []
        for module in modules:
            targets.extend(
                [
                    {
                        "module": module,
                        "path": "framework-res-hnext/res/drawable-xxhdpi/background_magic.9.png",
                    },
                    {
                        "module": module,
                        "path": "framework-res-hwext/res/drawable-xxhdpi/background_emui.9.png",
                    },
                ]
            )
        result.append(
            ResourceSlot(
                id=f"__synthetic__::background::{label}",
                module=modules[0],
                container="",
                resource_type="image",
                name=label,
                path="",
                category=label.replace("背景", "") or "常用背景",
                label=label,
                status="可能支持",
                risk="中",
                width=1220,
                height=2700,
                actual_format="PNG",
                extension=".png",
                synthetic=True,
                targets=targets,
            )
        )
    result.append(
        ResourceSlot(
            id="__unsupported__::wechat_main_background",
            module="com.tencent.mm",
            container="",
            resource_type="image",
            name="wechat_main_background",
            path="",
            category="微信",
            label="微信8.0.76主界面图片背景",
            status="当前版本不支持",
            risk="高",
            synthetic=True,
        )
    )
    return result


def scan_theme(path: Path) -> ThemeCatalog:
    path = Path(path)
    resources: list[ResourceSlot] = []
    warnings: list[dict] = []
    stats = Counter()
    modules = 0
    with ZipFile(path) as outer:
        outer_infos = outer.infolist()
        if len(outer_infos) > MAX_ARCHIVE_ENTRIES:
            raise ValueError(f"主题条目数量超过 {MAX_ARCHIVE_ENTRIES} 条")
        if any(info.file_size > MAX_ARCHIVE_ENTRY_BYTES for info in outer_infos if not info.is_dir()):
            raise ValueError(f"主题条目超过 {MAX_ARCHIVE_ENTRY_BYTES} 字节")
        if sum(info.file_size for info in outer_infos if not info.is_dir()) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError("主题解压总量超过限制")
        unsafe_paths = {
            info.filename for info in outer_infos if not is_safe_archive_path(info.filename.rstrip("/"))
        }
        for name in sorted(unsafe_paths):
            warnings.append({"kind": "unsafe_path", "path": name})
        blocked_paths = set(unsafe_paths)
        blocked_paths.update(_archive_blocked_paths(outer_infos, warnings))
        try:
            bad = None if blocked_paths else outer.testzip()
        except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
            warnings.append({"kind": "outer_crc_check", "message": str(exc)})
            bad = None
        if bad:
            warnings.append({"kind": "outer_crc", "path": bad})
            blocked_paths.add(bad)
        for info in outer_infos:
            if info.is_dir():
                continue
            if info.filename in blocked_paths:
                continue
            name = info.filename
            try:
                raw = outer.read(info)
            except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
                warnings.append({"kind": "outer_entry_read", "path": name, "message": str(exc)})
                continue
            if name in {"description.xml", "unlock/theme.xml"}:
                continue
            if name.startswith("wallpaper/") and _is_image_payload(name, raw):
                resources.append(_image_slot("__root__", name, raw, "wallpaper", warnings))
                stats["wallpaper_slots"] += 1
                continue
            if name.startswith("preview/") and _is_image_payload(name, raw):
                resources.append(_image_slot("__root__", name, raw, "preview", warnings))
                stats["preview_slots"] += 1
                continue
            try:
                with ZipFile(BytesIO(raw)):
                    pass
            except BadZipFile:
                continue
            except (OSError, RuntimeError, ValueError) as exc:
                warnings.append({"kind": "nested_zip_open", "path": name, "message": str(exc)})
                continue
            modules += 1
            slots, module_stats = _scan_module(name, raw, warnings)
            resources.extend(slots)
            stats.update(module_stats)
    synthetic = _synthetic_background_slots()
    resources.extend(synthetic)
    stats["synthetic_slots"] = len(synthetic)
    stats["modules"] = modules
    stats["resource_slots"] = len(resources)
    return ThemeCatalog(
        source_path=str(path),
        source_sha256=sha256_file(path),
        generated_at=datetime.now(timezone.utc).isoformat(),
        stats=dict(stats),
        warnings=warnings,
        resources=resources,
    )


SOURCE_COMPATIBILITY_WARNING_KINDS = frozenset({
    "duplicate_resource",
    "image_format_mismatch",
    "nonstandard_xml",
})


def source_compatibility_report(catalog: ThemeCatalog) -> dict:
    """Summarize recoverable source issues without treating them as export failures."""
    warning_items = [dict(item) for item in catalog.warnings if isinstance(item, dict)]
    by_kind = Counter()
    compatibility_items: list[dict] = []
    scan_integrity_items: list[dict] = []
    for item in warning_items:
        raw_kind = item.get("kind")
        kind = raw_kind if isinstance(raw_kind, str) and raw_kind else "unknown"
        by_kind[kind] += 1
        if kind in SOURCE_COMPATIBILITY_WARNING_KINDS:
            compatibility_items.append(item)
        else:
            scan_integrity_items.append(item)

    def stat(name: str) -> int:
        value = catalog.stats.get(name, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    return {
        "schema": 1,
        "source_path": catalog.source_path,
        "source_sha256": catalog.source_sha256,
        "generated_at": catalog.generated_at,
        "summary": {
            "modules": stat("modules"),
            "resource_slots": stat("resource_slots"),
            "total_warnings": len(warning_items),
            "compatibility_warnings": len(compatibility_items),
            "scan_integrity_warnings": len(scan_integrity_items),
            "by_kind": dict(sorted(by_kind.items())),
        },
        "compatibility": {
            "warning_kinds": sorted(SOURCE_COMPATIBILITY_WARNING_KINDS),
            "items": compatibility_items,
        },
        "scan_integrity": {
            "items": scan_integrity_items,
        },
        "strict_export_validation": {
            "performed": False,
            "note": "此报告只描述源主题的只读扫描结果；最终导出文件仍需单独通过严格验证。",
        },
    }


def _save_bounded_json(payload: dict, path: Path, *, too_large_message: str) -> None:
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("资源目录输出目标不是普通文件")
    ensure_no_symlink_parents(path, "资源目录输出目录不能包含符号链接")
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_no_symlink_parents(path, "资源目录输出目录不能包含符号链接")
    temp = unique_temp_path(path)
    if temp.is_symlink() or (temp.exists() and not temp.is_file()):
        raise ValueError("资源目录临时文件不是普通文件")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if len(encoded) > MAX_CATALOG_BYTES:
            raise ValueError(too_large_message)
        temp.write_bytes(encoded)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("资源目录输出目标不是普通文件")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_catalog(catalog: ThemeCatalog, path: Path) -> None:
    _save_bounded_json(
        catalog.to_dict(),
        path,
        too_large_message="保存的资源目录文件超过允许的大小限制",
    )


def save_source_compatibility_report(catalog: ThemeCatalog, path: Path) -> None:
    _save_bounded_json(
        source_compatibility_report(catalog),
        path,
        too_large_message="源主题兼容性报告超过允许的大小限制",
    )


def _validate_catalog_resource(resource: dict, index: int) -> None:
    for field in (*_RESOURCE_STRING_FIELDS, *_RESOURCE_OPTIONAL_STRING_FIELDS):
        value = resource.get(field)
        if isinstance(value, str) and (
            len(value) > _CATALOG_TEXT_MAX_CHARS
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError(f"资源目录第 {index} 条记录的 {field} 过长或包含控制字符")
    module = resource["module"]
    if not _CATALOG_MODULE_RE.fullmatch(module):
        raise ValueError(f"资源目录第 {index} 条记录的模块名不安全")
    if resource["resource_type"] not in _CATALOG_RESOURCE_TYPES:
        raise ValueError(f"资源目录第 {index} 条记录的资源类型不支持")
    synthetic = resource.get("synthetic", False)
    path = resource["path"]
    if path and not is_safe_archive_path(path):
        raise ValueError(f"资源目录第 {index} 条记录的资源路径不安全")
    container = resource["container"]
    if container and not is_safe_archive_path(container):
        raise ValueError(f"资源目录第 {index} 条记录的容器路径不安全")
    if not synthetic and not path:
        raise ValueError(f"资源目录第 {index} 条记录缺少资源路径")
    for field in ("width", "height"):
        value = resource.get(field)
        if value is not None and value > 16384:
            raise ValueError(f"资源目录第 {index} 条记录的 {field} 超过上限")
    targets = resource.get("targets", [])
    for target in targets:
        if not _CATALOG_MODULE_RE.fullmatch(target["module"]) or not is_safe_archive_path(target["path"]):
            raise ValueError(f"资源目录第 {index} 条记录的目标路径不安全")


def load_catalog(path: Path) -> ThemeCatalog:
    path = Path(path)
    with path.open("rb") as stream:
        encoded = stream.read(MAX_CATALOG_BYTES + 1)
    if len(encoded) > MAX_CATALOG_BYTES:
        raise ValueError("资源目录文件超过允许的大小限制")
    try:
        raw = json.loads(encoded.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("资源目录文件不是有效的 UTF-8 文本") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("资源目录文件不是有效的 JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("资源目录顶层必须是 JSON 对象")
    schema = raw.get("schema", 1)
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != 1:
        raise ValueError(f"不支持的资源目录格式版本：{schema}")
    resources = raw.get("resources", [])
    if not isinstance(resources, list):
        raise ValueError("资源目录字段 resources 必须是对象列表")
    required = {"id", "module", "container", "resource_type", "name", "path", "category", "label"}
    if any(not isinstance(item, dict) or not required.issubset(item) for item in resources):
        raise ValueError("资源目录中的资源记录格式无效")
    resource_ids: set[str] = set()
    for index, resource in enumerate(resources, start=1):
        if any(not isinstance(resource[field], str) for field in _RESOURCE_STRING_FIELDS):
            raise ValueError("资源目录中的资源文字字段类型无效")
        for field in _RESOURCE_OPTIONAL_STRING_FIELDS:
            if field in resource and resource[field] is not None and not isinstance(resource[field], str):
                raise ValueError(f"资源目录中的资源字段 {field} 类型无效")
        for field in ("ninepatch", "synthetic"):
            if field in resource and not isinstance(resource[field], bool):
                raise ValueError(f"资源目录中的资源字段 {field} 类型无效")
        for field in ("width", "height"):
            if field in resource and resource[field] is not None and (
                isinstance(resource[field], bool) or not isinstance(resource[field], int) or resource[field] < 1
            ):
                raise ValueError(f"资源目录中的资源字段 {field} 类型无效")
        if "occurrences" in resource and (
            isinstance(resource["occurrences"], bool)
            or not isinstance(resource["occurrences"], int)
            or resource["occurrences"] < 1
        ):
            raise ValueError("资源目录中的资源字段 occurrences 类型无效")
        chunks = resource.get("png_chunks", {})
        if not isinstance(chunks, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in chunks.items()):
            raise ValueError("资源目录中的资源字段 png_chunks 类型无效")
        targets = resource.get("targets", [])
        if not isinstance(targets, list) or any(
            not isinstance(target, dict)
            or not isinstance(target.get("module"), str)
            or not isinstance(target.get("path"), str)
            for target in targets
        ):
            raise ValueError("资源目录中的资源字段 targets 类型无效")
        if resource["id"] in resource_ids:
            raise ValueError("资源目录中的资源 ID 重复")
        resource_ids.add(resource["id"])
        _validate_catalog_resource(resource, index)
    for field in ("source_path", "source_sha256", "generated_at"):
        if field in raw and not isinstance(raw[field], str):
            raise ValueError(f"资源目录字段 {field} 必须是文字")
    stats = raw.get("stats", {})
    warnings = raw.get("warnings", [])
    if not isinstance(stats, dict) or any(
        not isinstance(key, str)
        or isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for key, value in stats.items()
    ):
        raise ValueError("资源目录的统计字段格式无效")
    if not isinstance(warnings, list) or any(not isinstance(item, dict) for item in warnings):
        raise ValueError("资源目录的统计或警告字段格式无效")
    return ThemeCatalog.from_dict(raw)
