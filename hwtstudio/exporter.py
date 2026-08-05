from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from .blank import blank_entries
from .common import honor_module_name, honor_resource_name, honor_resource_path, honor_resource_paths
from .imageops import render_image, render_placeholder
from .models import ResourceChange, ResourceSlot, ThemeCatalog, ThemeProject
from .paths import ensure_no_symlink_parents, unique_temp_path
from .validation import validate_change_value, validate_custom_slot, validate_theme


def safe_filename(value: str) -> str:
    invalid = '<>:"/\\|?*'
    result = "".join("_" if c in invalid or ord(c) < 32 or ord(c) == 127 else c for c in value)
    result = result.strip().strip(".")[:80].rstrip(" .")
    return result or "我的主题"


def default_export_name(project: ThemeProject) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"空白主题_{safe_filename(project.name)}_{timestamp}.hwt"


def _slot_map(catalog: ThemeCatalog, project: ThemeProject) -> dict[str, ResourceSlot]:
    return {slot.id: slot for slot in [*catalog.resources, *project.custom_resources]}


def _slot_target(slot: ResourceSlot, scanned_ids: set[str]) -> tuple[str, str, str, str]:
    """Return the output module, XML path/name, and image path for a slot."""
    if slot.synthetic or slot.id not in scanned_ids:
        return slot.module, slot.container, slot.name, slot.path
    return (
        honor_module_name(slot.module),
        honor_resource_path(slot.container),
        honor_resource_name(slot.name),
        honor_resource_path(slot.path),
    )


def _same_path(left: Path, right: Path) -> bool:
    try:
        if left.exists() and right.exists():
            return os.path.samefile(left, right)
    except OSError:
        pass
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _ExportTarget:
    key: tuple[str, ...]
    slot: ResourceSlot
    change: ResourceChange
    value: str | None
    signature: tuple
    priority: int


def _target_keys(slot: ResourceSlot, scanned_ids: set[str]) -> list[tuple[str, ...]]:
    if slot.resource_type in {"color", "bool", "integer", "dimen", "string"}:
        target_module, target_container, target_name, _ = _slot_target(slot, scanned_ids)
        return [("xml", target_module, target_container, slot.resource_type, target_name)]
    if slot.resource_type not in {"image", "icon", "wallpaper", "preview"}:
        return []
    if slot.synthetic:
        return [("image", target["module"], target["path"]) for target in slot.targets]
    target_module, _, _, target_path = _slot_target(slot, scanned_ids)
    if slot.id in scanned_ids:
        target_paths = honor_resource_paths(slot.module, slot.path)
    else:
        target_paths = (target_path,)
    return [("image", target_module, path) for path in target_paths]


def _slot_priority(slot: ResourceSlot, scanned_ids: set[str]) -> int:
    """Prefer native Honor resources over source resources renamed for Honor."""
    if slot.synthetic or slot.id not in scanned_ids:
        return -1
    original = (slot.module, slot.container, slot.name, slot.path)
    converted = _slot_target(slot, scanned_ids)
    if slot.resource_type in {"image", "icon", "wallpaper", "preview"}:
        # A fan-out is a compatibility source even when its original path is
        # otherwise unchanged; native Honor slots must win those targets.
        if len(honor_resource_paths(slot.module, slot.path)) > 1:
            return 1
    return 2 if converted == original else 1


def _change_signature(
    slot: ResourceSlot,
    change: ResourceChange,
    value: str | None,
    file_digests: dict[str, str],
) -> tuple:
    if slot.resource_type in {"color", "bool", "integer", "dimen", "string"}:
        return ("value", value)
    render_shape = (
        slot.width,
        slot.height,
        slot.actual_format,
        slot.extension,
        tuple(sorted(slot.png_chunks.items())),
    )
    if change.source_kind == "placeholder":
        return ("placeholder", render_shape)
    if change.source_kind != "file" or not change.source_file:
        return ("image", change.source_kind, change.source_file or "")
    source = Path(change.source_file)
    cache_key = os.path.normcase(str(source.resolve()))
    digest = file_digests.get(cache_key)
    if digest is None:
        digest = _sha256_file(source)
        file_digests[cache_key] = digest
    return (
        "image",
        digest,
        render_shape,
        change.fit,
        change.focus_x,
        change.focus_y,
        change.enhance,
        change.enhance_strength,
    )


def _conflict_error(key: tuple[str, ...], candidates: list[_ExportTarget]) -> dict:
    return {
        "kind": "duplicate_target",
        "slot_id": candidates[0].slot.id,
        "other_slot_id": candidates[1].slot.id,
        "slot_ids": [candidate.slot.id for candidate in candidates],
        "target": list(key),
        "message": "多个修改目标内容不同，且无法根据资源来源安全确定覆盖顺序",
    }


def _choose_targets(
    grouped: dict[tuple[str, ...], list[_ExportTarget]],
    warnings: list[dict],
    errors: list[dict],
) -> list[_ExportTarget]:
    selected: list[_ExportTarget] = []
    for key, candidates in grouped.items():
        if len(candidates) == 1:
            selected.append(candidates[0])
            continue
        signatures = {candidate.signature for candidate in candidates}
        winner = max(candidates, key=lambda candidate: candidate.priority)
        if len(signatures) == 1:
            warnings.append(
                {
                    "kind": "duplicate_target_merged",
                    "target": list(key),
                    "slot_ids": [candidate.slot.id for candidate in candidates],
                    "selected_slot_id": winner.slot.id,
                    "message": "多个兼容资源内容一致，已合并为一个导出目标",
                }
            )
            selected.append(winner)
            continue
        if all(candidate.priority > 0 for candidate in candidates) and sum(
            candidate.priority == winner.priority for candidate in candidates
        ) == 1:
            discarded = [candidate.slot.id for candidate in candidates if candidate is not winner]
            warnings.append(
                {
                    "kind": "duplicate_target_resolved",
                    "target": list(key),
                    "slot_ids": [candidate.slot.id for candidate in candidates],
                    "selected_slot_id": winner.slot.id,
                    "discarded_slot_ids": discarded,
                    "policy": "荣耀原生资源优先",
                    "message": "目标内容不同，已按荣耀原生资源优先策略选择一个写入目标",
                }
            )
            selected.append(winner)
            continue
        errors.append(_conflict_error(key, candidates))
    return selected


def _prepare_export(project: ThemeProject, catalog: ThemeCatalog) -> dict:
    slots = _slot_map(catalog, project)
    scanned_ids = {slot.id for slot in catalog.resources}
    errors: list[dict] = []
    warnings: list[dict] = []
    grouped: dict[tuple[str, ...], list[_ExportTarget]] = defaultdict(list)
    custom_ids = [slot.id for slot in project.custom_resources]
    known_ids = {slot.id for slot in catalog.resources}
    file_digests: dict[str, str] = {}
    for slot in project.custom_resources:
        try:
            validate_custom_slot(slot)
        except ValueError as exc:
            errors.append({"kind": "invalid_custom_slot", "slot_id": slot.id, "message": str(exc)})
        if slot.id in known_ids or custom_ids.count(slot.id) > 1:
            errors.append({"kind": "duplicate_slot_id", "slot_id": slot.id})

    value_count = image_count = skipped_count = 0
    for slot_id, change in project.changes.items():
        if not change.enabled:
            continue
        slot = slots.get(slot_id)
        if not slot:
            warnings.append({"kind": "missing_slot", "slot_id": slot_id})
            skipped_count += 1
            continue
        if slot.status == "当前版本不支持":
            warnings.append({"kind": "unsupported", "slot_id": slot_id, "reason": slot.status})
            skipped_count += 1
            continue
        if slot.resource_type in {"color", "bool", "integer", "dimen", "string"}:
            if change.value is None:
                errors.append({"kind": "missing_value", "slot_id": slot_id})
                continue
            try:
                value = validate_change_value(slot.resource_type, change.value)
            except ValueError as exc:
                errors.append({"kind": "invalid_value", "slot_id": slot_id, "message": str(exc)})
                continue
            target_keys = _target_keys(slot, scanned_ids)
        elif slot.resource_type in {"image", "icon", "wallpaper", "preview"}:
            if change.source_kind == "placeholder":
                pass
            elif change.source_kind != "file":
                errors.append({"kind": "invalid_source_kind", "slot_id": slot_id, "value": change.source_kind})
            elif not change.source_file or not Path(change.source_file).is_file():
                errors.append({"kind": "missing_image", "slot_id": slot_id, "path": change.source_file or ""})
                continue
            target_keys = _target_keys(slot, scanned_ids)
            value = None
        else:
            warnings.append({"kind": "unsupported_type", "slot_id": slot_id, "type": slot.resource_type})
            skipped_count += 1
            continue
        if not slot.synthetic and slot.id in scanned_ids and len(target_keys) > 1:
            warnings.append(
                {
                    "kind": "resource_fanout",
                    "slot_id": slot.id,
                    "source": {"module": slot.module, "path": slot.path},
                    "targets": [{"module": key[1], "path": key[2]} for key in target_keys],
                    "policy": "华为资源复制到全部兼容的荣耀目标",
                    "message": "一个华为资源将复制到多个荣耀目标；每个目标单独执行冲突审计",
                }
            )
        signature = _change_signature(slot, change, value, file_digests)
        priority = _slot_priority(slot, scanned_ids)
        for key in target_keys:
            grouped[key].append(_ExportTarget(key, slot, change, value, signature, priority))
    selected = _choose_targets(grouped, warnings, errors)
    value_count = sum(target.key[0] == "xml" for target in selected)
    image_count = sum(target.key[0] == "image" for target in selected)
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "enabled_changes": sum(1 for change in project.changes.values() if change.enabled),
        "value_targets": value_count,
        "image_targets": image_count,
        "skipped": skipped_count,
        "_targets": selected,
    }


def preflight_export(project: ThemeProject, catalog: ThemeCatalog) -> dict:
    result = _prepare_export(project, catalog)
    result.pop("_targets", None)
    return result


def _xml_bytes(items: dict[tuple[str, str], str]) -> bytes:
    root = etree.Element("resources")
    for (resource_type, name), value in sorted(items.items(), key=lambda pair: (pair[0][0], pair[0][1].lower())):
        node = etree.SubElement(root, resource_type, name=name)
        node.text = value
    return etree.tostring(root, xml_declaration=True, encoding="utf-8", pretty_print=True)


def _module_bytes(files: dict[str, bytes], xml_items: dict[str, dict[tuple[str, str], str]]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for path, items in sorted(xml_items.items()):
            if items:
                archive.writestr(path, _xml_bytes(items))
        for path, data in sorted(files.items()):
            archive.writestr(path, data)
    return output.getvalue()


def export_theme(project: ThemeProject, catalog: ThemeCatalog, output: Path) -> tuple[Path, dict]:
    output = Path(output)
    if output.name.lower().endswith(".report.json"):
        raise ValueError("导出文件名不能以 .report.json 结尾，请选择 .hwt 文件名")
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise ValueError("导出文件目标不是普通文件")
    ensure_no_symlink_parents(output, "导出目录不能包含符号链接")
    if catalog.source_path and _same_path(output, Path(catalog.source_path)):
        raise ValueError("不能覆盖资源目录对应的原始主题，请选择新的导出文件名")
    prepared = _prepare_export(project, catalog)
    export_targets = prepared.pop("_targets")
    preflight = prepared
    if not preflight["valid"]:
        raise ValueError("导出预检失败：" + json.dumps(preflight["errors"][:8], ensure_ascii=False))
    output.parent.mkdir(parents=True, exist_ok=True)
    ensure_no_symlink_parents(output, "导出目录不能包含符号链接")
    root_entries = blank_entries(project.title, project.author, project.designer, project.version, project.screen)
    module_files: dict[str, dict[str, bytes]] = defaultdict(dict)
    module_xml: dict[str, dict[str, dict[tuple[str, str], str]]] = defaultdict(lambda: defaultdict(dict))
    applied: list[dict] = []
    skipped = [
        {
            "slot_id": item["slot_id"],
            "reason": item.get("reason") or ("资源目录中不存在" if item["kind"] == "missing_slot" else item.get("kind", "已跳过")),
        }
        for item in preflight["warnings"]
        if item["kind"] in {"missing_slot", "unsupported"}
    ]

    rendered_by_slot: dict[str, bytes] = {}
    for target in export_targets:
        key = target.key
        if key[0] == "xml":
            _, target_module, target_container, resource_type, target_name = key
            module_xml[target_module][target_container][(resource_type, target_name)] = target.value or ""
            applied.append(
                {
                    "slot_id": target.slot.id,
                    "module": target_module,
                    "path": target_container,
                    "value": target.value,
                }
            )
            continue
        _, target_module, target_path = key
        rendered = rendered_by_slot.get(target.slot.id)
        if rendered is None:
            if target.change.source_kind == "placeholder":
                rendered = render_placeholder(target.slot)
            else:
                source = Path(target.change.source_file)
                rendered = render_image(source, target.slot, target.change)
                try:
                    current_digest = _sha256_file(source)
                except OSError as exc:
                    raise ValueError("图片源文件在导出期间不可用，请重试") from exc
                if current_digest != target.signature[1]:
                    raise ValueError("图片源文件在导出期间发生变化，请重试")
            rendered_by_slot[target.slot.id] = rendered
        if target_module == "__root__":
            root_entries[target_path] = rendered
        else:
            module_files[target_module][target_path] = rendered
        applied.append({"slot_id": target.slot.id, "module": target_module, "path": target_path})

    temp = unique_temp_path(output)
    if temp.is_symlink() or (temp.exists() and not temp.is_file()):
        raise ValueError("导出临时文件不是普通文件")
    temp.unlink(missing_ok=True)
    try:
        modules = sorted(set(module_files) | set(module_xml))
        built_modules: dict[str, bytes] = {}
        for module in modules:
            data = _module_bytes(module_files[module], module_xml[module])
            if module == "icons":
                root_entries["icons"] = data
            else:
                built_modules[module] = data
        with ZipFile(temp, "w", ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in root_entries.items():
                archive.writestr(name, data)
            for module, data in built_modules.items():
                archive.writestr(module, data)

        validation = validate_theme(temp)
        if not validation["valid"]:
            raise ValueError("导出的主题未通过验证：" + json.dumps(validation["errors"][:5], ensure_ascii=False))
        if output.is_symlink() or (output.exists() and not output.is_file()):
            raise ValueError("导出文件目标不是普通文件")
        os.replace(temp, output)
    finally:
        if not temp.is_symlink() and (not temp.exists() or temp.is_file()):
            temp.unlink(missing_ok=True)
    digest = _sha256_file(output)
    report = {
        "schema": 1,
        "output": str(output),
        "sha256": digest,
        "source_catalog_sha256": catalog.source_sha256,
        "generated_at": datetime.now().isoformat(),
        "applied_count": len(applied),
        "applied": applied,
        "skipped": skipped,
        "validation": validation,
        "preflight": preflight,
        "module_count": len(modules),
        "file_size": output.stat().st_size,
    }
    report_path = output.with_suffix(".report.json")
    report_temp = unique_temp_path(report_path)
    try:
        if report_path.is_symlink() or (report_path.exists() and not report_path.is_file()):
            raise OSError("导出报告目标不是普通文件")
        ensure_no_symlink_parents(report_path, "导出报告目录不能包含符号链接")
        if report_temp.is_symlink() or (report_temp.exists() and not report_temp.is_file()):
            raise OSError("导出报告临时文件不是普通文件")
        report_temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if report_path.is_symlink() or (report_path.exists() and not report_path.is_file()):
            raise OSError("导出报告目标不是普通文件")
        os.replace(report_temp, report_path)
    except (OSError, TypeError, ValueError) as exc:
        report["report_warning"] = f"导出成功，但报告写入失败：{exc}"
    finally:
        if not report_temp.is_symlink() and (not report_temp.exists() or report_temp.is_file()):
            report_temp.unlink(missing_ok=True)
    return output, report
