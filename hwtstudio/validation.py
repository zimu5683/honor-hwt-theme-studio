from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from .archive_safety import (
    archive_data_overlaps,
    archive_local_header_issues,
    archive_path_overlaps,
    compression_ratio,
    duplicate_names,
    duplicate_normalized_names,
    is_symlink,
    zip64_inconsistencies,
)
from .catalog import detect_format
from .common import (
    MAX_ARCHIVE_COMPRESSION_RATIO,
    MAX_ARCHIVE_ENTRIES,
    MAX_ARCHIVE_ENTRY_BYTES,
    MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    is_safe_archive_path,
)
from .models import ResourceSlot
from .xmlutil import parse_xml


COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")
INTEGER_RE = re.compile(r"^[+-]?\d+$")
DIMEN_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:px|dp|dip|sp|pt|in|mm)$", re.IGNORECASE)
REFERENCE_RE = re.compile(r"^(?:@|\?)[A-Za-z0-9_.:/-]+$")
MODULE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
RESERVED_ROOT_MODULES = {"description.xml", "unlock", "wallpaper", "preview"}
VALUE_TYPES = {"color", "bool", "integer", "dimen", "string"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_TYPES = {"image", "icon", "wallpaper", "preview"}
CUSTOM_RESOURCE_TYPES = VALUE_TYPES | IMAGE_TYPES
_RESOURCE_TEXT_FIELDS = (
    "id",
    "module",
    "container",
    "resource_type",
    "name",
    "path",
    "category",
    "label",
)
_RESOURCE_OPTIONAL_TEXT_FIELDS = ("status", "risk", "mode", "actual_format", "extension")


def normalize_color(value: str) -> str:
    value = value.strip()
    if not value.startswith("#"):
        value = "#" + value
    if len(value) not in {7, 9} or not COLOR_RE.fullmatch(value):
        raise ValueError("颜色必须是 #RRGGBB 或 #AARRGGBB")
    # HWT files conventionally use #AARRGGBB; retain explicitly supplied RGB values.
    return value.upper()


def validate_change_value(resource_type: str, value: str) -> str:
    value = value.strip()
    if resource_type == "color":
        return normalize_color(value)
    if resource_type == "bool":
        lowered = value.lower()
        if lowered not in {"true", "false"}:
            raise ValueError("布尔值必须是 true 或 false")
        return lowered
    if resource_type == "integer" and not (INTEGER_RE.fullmatch(value) or REFERENCE_RE.fullmatch(value)):
        raise ValueError("整数必须是十进制整数或合法资源引用")
    if resource_type == "dimen" and not (DIMEN_RE.fullmatch(value) or REFERENCE_RE.fullmatch(value)):
        raise ValueError("尺寸必须包含 px、dp、dip、sp、pt、in 或 mm 单位，或使用合法资源引用")
    return value


def validate_custom_slot(slot: ResourceSlot) -> None:
    if not isinstance(slot, ResourceSlot):
        raise ValueError("自定义资源记录格式无效")
    for field in _RESOURCE_TEXT_FIELDS:
        if not isinstance(getattr(slot, field), str):
            raise ValueError(f"自定义资源的 {field} 字段类型无效")
    for field in _RESOURCE_OPTIONAL_TEXT_FIELDS:
        value = getattr(slot, field)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"自定义资源的 {field} 字段类型无效")
    for field in ("ninepatch", "synthetic"):
        if not isinstance(getattr(slot, field), bool):
            raise ValueError(f"自定义资源的 {field} 字段类型无效")
    for field in ("width", "height"):
        value = getattr(slot, field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16384
        ):
            raise ValueError(f"自定义资源的 {field} 字段必须是 1 到 16384 之间的整数")
    if isinstance(slot.occurrences, bool) or not isinstance(slot.occurrences, int) or slot.occurrences < 1:
        raise ValueError("自定义资源的 occurrences 字段必须是正整数")
    if not isinstance(slot.png_chunks, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in slot.png_chunks.items()
    ):
        raise ValueError("自定义资源的 png_chunks 字段类型无效")
    if not isinstance(slot.targets, list) or any(
        not isinstance(target, dict)
        or not isinstance(target.get("module"), str)
        or not isinstance(target.get("path"), str)
        for target in slot.targets
    ):
        raise ValueError("自定义资源的 targets 字段类型无效")
    if not slot.id.strip():
        raise ValueError("自定义资源 ID 不能为空")
    if not MODULE_RE.fullmatch(slot.module) or ".." in slot.module or slot.module in RESERVED_ROOT_MODULES:
        raise ValueError("模块名只能包含字母、数字、点、下划线和连字符，且不能与主题根条目冲突")
    if slot.resource_type not in CUSTOM_RESOURCE_TYPES:
        raise ValueError("自定义资源类型不支持")
    if not slot.name.strip():
        raise ValueError("资源名不能为空")
    if slot.container and not is_safe_archive_path(slot.container):
        raise ValueError("资源容器路径必须是安全的 ZIP 相对路径")
    if not is_safe_archive_path(slot.path):
        raise ValueError("资源路径必须是安全的 ZIP 相对路径，不能包含反斜杠、绝对路径或 ..")
    if slot.resource_type in VALUE_TYPES and Path(slot.path).suffix.lower() != ".xml":
        raise ValueError("颜色、布尔、整数、尺寸和文字资源必须写入 .xml 文件")
    if slot.resource_type in IMAGE_TYPES and Path(slot.path).suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("图片路径必须以 .png、.jpg、.jpeg 或 .webp 结尾")
    for target in slot.targets:
        target_module = target["module"]
        target_path = target["path"]
        if not MODULE_RE.fullmatch(target_module) or target_module in RESERVED_ROOT_MODULES:
            raise ValueError("自定义资源目标模块名不安全")
        if not is_safe_archive_path(target_path):
            raise ValueError("自定义资源目标路径不安全")


def _validate_resource_xml(raw: bytes, *, module: str, path: str, errors: list[dict]) -> None:
    try:
        root = parse_xml(raw)
    except Exception as exc:
        errors.append({"kind": "nested_xml", "module": module, "path": path, "message": str(exc)})
        return
    if root.tag != "resources":
        errors.append({"kind": "xml_root", "module": module, "path": path, "actual": str(root.tag)})
        return
    seen = set()
    for node in root:
        if not isinstance(node.tag, str):
            continue
        name = (node.get("name") or "").strip()
        key = (node.tag, name)
        if not name:
            errors.append({"kind": "missing_name", "module": module, "path": path, "type": node.tag})
        if key in seen:
            errors.append({"kind": "duplicate", "module": module, "path": path, "name": name, "type": node.tag})
        seen.add(key)
        if node.tag not in VALUE_TYPES:
            errors.append({"kind": "unsupported_node", "module": module, "path": path, "name": name, "type": node.tag})
            continue
        try:
            validate_change_value(node.tag, node.text or "")
        except ValueError as exc:
            errors.append({"kind": node.tag, "module": module, "path": path, "name": name, "message": str(exc)})


def validate_theme(path: Path) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    modules = 0
    resources = 0
    try:
        with ZipFile(path) as outer:
            outer_infos = outer.infolist()
            outer_size_blocked = False
            outer_expanded = 0
            if len(outer_infos) > MAX_ARCHIVE_ENTRIES:
                errors.append({"kind": "too_many_entries", "path": str(path), "limit": MAX_ARCHIVE_ENTRIES})
                outer_size_blocked = True
            for info in outer_infos:
                if info.is_dir():
                    continue
                if info.file_size > MAX_ARCHIVE_ENTRY_BYTES:
                    errors.append({"kind": "oversized_entry", "path": info.filename})
                    outer_size_blocked = True
                    continue
                outer_expanded += info.file_size
            if outer_expanded > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                errors.append({"kind": "archive_too_large", "path": str(path)})
                outer_size_blocked = True
            names = set(outer.namelist())
            unsafe_outer_paths = set()
            outer_overlaps = archive_path_overlaps(outer.infolist())
            outer_data_overlaps = archive_data_overlaps(outer.infolist(), outer.fp)
            for duplicate in duplicate_names(outer.infolist()):
                errors.append({"kind": "duplicate_zip_entry", "path": duplicate})
            for duplicate in duplicate_normalized_names(outer.infolist()):
                errors.append({"kind": "duplicate_normalized_zip_entry", "path": duplicate})
            for parent, path_name in outer_overlaps:
                errors.append({"kind": "path_overlap", "path": path_name, "parent": parent})
            for parent, path_name in outer_data_overlaps:
                errors.append({"kind": "data_overlap", "path": path_name, "overlaps": parent})
            outer_local_header_issues = archive_local_header_issues(outer.infolist(), outer.fp)
            for path_name, issue in outer_local_header_issues:
                errors.append({"kind": "local_header_mismatch", "path": path_name, "message": issue})
            for name in outer.namelist():
                if not is_safe_archive_path(name.rstrip("/")):
                    errors.append({"kind": "unsafe_path", "path": name})
                    unsafe_outer_paths.add(name)
            outer_read_blocked = bool(
                outer_overlaps or outer_data_overlaps or outer_local_header_issues
            )
            for info in outer_infos:
                if info.is_dir():
                    continue
                ratio = compression_ratio(info)
                if ratio is not None and ratio > MAX_ARCHIVE_COMPRESSION_RATIO:
                    errors.append({
                        "kind": "compression_ratio",
                        "path": info.filename,
                        "ratio": ratio,
                        "limit": MAX_ARCHIVE_COMPRESSION_RATIO,
                    })
                    outer_read_blocked = True
                if is_symlink(info):
                    errors.append({"kind": "symlink_entry", "path": info.filename})
                    outer_read_blocked = True
                for issue in zip64_inconsistencies(info):
                    errors.append({"kind": "zip64_inconsistent", "path": info.filename, "message": issue})
                    outer_read_blocked = True
            try:
                bad = None if outer_size_blocked or outer_read_blocked or unsafe_outer_paths else outer.testzip()
            except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
                errors.append({"kind": "outer_crc_check", "message": str(exc)})
                bad = None
            if bad:
                errors.append({"kind": "crc", "path": bad})
            required = {
                "description.xml",
                "unlock/theme.xml",
                "wallpaper/home_wallpaper_0.jpg",
                "wallpaper/unlock_wallpaper_0.jpg",
                # Required by Honor Theme Manager's isValidThemeInfo() for
                # external/local HWT packages.  Merely having preview/* files
                # is not enough: the directory entry itself must exist.
                "preview/",
                "icons",
            }
            for missing in sorted(required - names):
                errors.append({"kind": "missing_required", "path": missing})
            for info in outer_infos:
                if info.is_dir():
                    continue
                if info.filename in unsafe_outer_paths:
                    continue
                if outer_size_blocked or outer_read_blocked or info.file_size > MAX_ARCHIVE_ENTRY_BYTES or outer_expanded > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    continue
                try:
                    raw = outer.read(info)
                except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
                    errors.append({"kind": "entry_read", "path": info.filename, "message": str(exc)})
                    continue
                if info.filename.endswith(".xml"):
                    try:
                        parse_xml(raw)
                    except Exception as exc:
                        errors.append({"kind": "xml", "path": info.filename, "message": str(exc)})
                suffix = Path(info.filename).suffix.lower()
                if suffix in IMAGE_SUFFIXES:
                    fmt = detect_format(raw)
                    expected = "PNG" if suffix == ".png" else "JPEG" if suffix in {".jpg", ".jpeg"} else "WEBP"
                    if fmt != expected:
                        errors.append({"kind": "image_format", "path": info.filename, "expected": expected, "actual": fmt})
                try:
                    with ZipFile(BytesIO(raw)) as module:
                        modules += 1
                        nested_infos = module.infolist()
                        nested_size_blocked = False
                        nested_expanded = 0
                        if len(nested_infos) > MAX_ARCHIVE_ENTRIES:
                            errors.append({"kind": "too_many_nested_entries", "module": info.filename, "limit": MAX_ARCHIVE_ENTRIES})
                            nested_size_blocked = True
                        for child in nested_infos:
                            if child.is_dir():
                                continue
                            if child.file_size > MAX_ARCHIVE_ENTRY_BYTES:
                                errors.append({"kind": "oversized_nested_entry", "module": info.filename, "path": child.filename})
                                nested_size_blocked = True
                                continue
                            nested_expanded += child.file_size
                        if nested_expanded > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                            errors.append({"kind": "nested_archive_too_large", "module": info.filename})
                            nested_size_blocked = True
                        unsafe_nested_paths = set()
                        nested_overlaps = archive_path_overlaps(nested_infos)
                        nested_data_overlaps = archive_data_overlaps(nested_infos, module.fp)
                        for child in nested_infos:
                            if not is_safe_archive_path(child.filename.rstrip("/")):
                                errors.append({"kind": "unsafe_nested_path", "module": info.filename, "path": child.filename})
                                unsafe_nested_paths.add(child.filename)
                        for duplicate in duplicate_names(nested_infos):
                            errors.append({"kind": "duplicate_nested_entry", "module": info.filename, "path": duplicate})
                        for duplicate in duplicate_normalized_names(nested_infos):
                            errors.append({"kind": "duplicate_normalized_nested_entry", "module": info.filename, "path": duplicate})
                        for parent, path_name in nested_overlaps:
                            errors.append({
                                "kind": "nested_path_overlap",
                                "module": info.filename,
                                "path": path_name,
                                "parent": parent,
                            })
                        for parent, path_name in nested_data_overlaps:
                            errors.append({
                                "kind": "nested_data_overlap",
                                "module": info.filename,
                                "path": path_name,
                                "overlaps": parent,
                            })
                        nested_local_header_issues = archive_local_header_issues(nested_infos, module.fp)
                        for path_name, issue in nested_local_header_issues:
                            errors.append({
                                "kind": "nested_local_header_mismatch",
                                "module": info.filename,
                                "path": path_name,
                                "message": issue,
                            })
                        nested_read_blocked = bool(
                            nested_overlaps or nested_data_overlaps or nested_local_header_issues
                        )
                        for child in nested_infos:
                            if child.is_dir():
                                continue
                            ratio = compression_ratio(child)
                            if ratio is not None and ratio > MAX_ARCHIVE_COMPRESSION_RATIO:
                                errors.append({
                                    "kind": "nested_compression_ratio",
                                    "module": info.filename,
                                    "path": child.filename,
                                    "ratio": ratio,
                                    "limit": MAX_ARCHIVE_COMPRESSION_RATIO,
                                })
                                nested_read_blocked = True
                            if is_symlink(child):
                                errors.append({
                                    "kind": "nested_symlink_entry",
                                    "module": info.filename,
                                    "path": child.filename,
                                })
                                nested_read_blocked = True
                            for issue in zip64_inconsistencies(child):
                                errors.append({
                                    "kind": "nested_zip64_inconsistent",
                                    "module": info.filename,
                                    "path": child.filename,
                                    "message": issue,
                                })
                                nested_read_blocked = True
                        try:
                            nested_bad = None if nested_size_blocked or nested_read_blocked or unsafe_nested_paths else module.testzip()
                        except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
                            errors.append({"kind": "nested_crc_check", "module": info.filename, "message": str(exc)})
                            nested_bad = None
                        if nested_bad:
                            errors.append({"kind": "nested_crc", "module": info.filename, "path": nested_bad})
                        for child in nested_infos:
                            if child.is_dir():
                                continue
                            if child.filename in unsafe_nested_paths:
                                continue
                            if nested_size_blocked or nested_read_blocked or child.file_size > MAX_ARCHIVE_ENTRY_BYTES or nested_expanded > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                                continue
                            try:
                                child_raw = module.read(child)
                            except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
                                errors.append({
                                    "kind": "nested_entry_read",
                                    "module": info.filename,
                                    "path": child.filename,
                                    "message": str(exc),
                                })
                                continue
                            if Path(child.filename).suffix.lower() == ".xml":
                                try:
                                    parsed = parse_xml(child_raw)
                                    resources += len(parsed)
                                except Exception:
                                    pass
                                _validate_resource_xml(child_raw, module=info.filename, path=child.filename, errors=errors)
                            suffix = Path(child.filename).suffix.lower()
                            if suffix in IMAGE_SUFFIXES:
                                fmt = detect_format(child_raw)
                                expected = "PNG" if suffix == ".png" else "JPEG" if suffix in {".jpg", ".jpeg"} else "WEBP"
                                if fmt != expected:
                                    errors.append({"kind": "image_format", "module": info.filename, "path": child.filename, "expected": expected, "actual": fmt})
                except BadZipFile:
                    pass
                except (OSError, RuntimeError, ValueError) as exc:
                    errors.append({"kind": "nested_zip", "module": info.filename, "message": str(exc)})
    except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
        errors.append({"kind": "outer_zip", "message": str(exc)})
    return {"valid": not errors, "errors": errors, "warnings": warnings, "modules": modules, "resource_nodes": resources}
