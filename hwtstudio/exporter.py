from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from .blank import blank_entries
from .imageops import render_image, render_placeholder
from .models import ResourceSlot, ThemeCatalog, ThemeProject
from .validation import validate_change_value, validate_custom_slot, validate_theme


def safe_filename(value: str) -> str:
    invalid = '<>:"/\\|?*'
    result = "".join("_" if c in invalid else c for c in value).strip().strip(".")
    return result or "我的主题"


def default_export_name(project: ThemeProject) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"空白主题_{safe_filename(project.name)}_{timestamp}.hwt"


def _slot_map(catalog: ThemeCatalog, project: ThemeProject) -> dict[str, ResourceSlot]:
    return {slot.id: slot for slot in [*catalog.resources, *project.custom_resources]}


def _same_path(left: Path, right: Path) -> bool:
    try:
        if left.exists() and right.exists():
            return os.path.samefile(left, right)
    except OSError:
        pass
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def preflight_export(project: ThemeProject, catalog: ThemeCatalog) -> dict:
    slots = _slot_map(catalog, project)
    errors: list[dict] = []
    warnings: list[dict] = []
    targets: dict[tuple, str] = {}
    custom_ids = [slot.id for slot in project.custom_resources]
    known_ids = {slot.id for slot in catalog.resources}
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
            warnings.append({"kind": "unsupported", "slot_id": slot_id})
            skipped_count += 1
            continue
        if slot.resource_type in {"color", "bool", "integer", "dimen", "string"}:
            if change.value is None:
                errors.append({"kind": "missing_value", "slot_id": slot_id})
                continue
            try:
                validate_change_value(slot.resource_type, change.value)
            except ValueError as exc:
                errors.append({"kind": "invalid_value", "slot_id": slot_id, "message": str(exc)})
            target_keys = [("xml", slot.module, slot.container, slot.resource_type, slot.name)]
            value_count += 1
        elif slot.resource_type in {"image", "icon", "wallpaper", "preview"}:
            if change.source_kind == "placeholder":
                pass
            elif change.source_kind != "file":
                errors.append({"kind": "invalid_source_kind", "slot_id": slot_id, "value": change.source_kind})
            elif not change.source_file or not Path(change.source_file).is_file():
                errors.append({"kind": "missing_image", "slot_id": slot_id, "path": change.source_file or ""})
            if slot.synthetic:
                target_keys = [("image", target["module"], target["path"]) for target in slot.targets]
            else:
                target_keys = [("image", slot.module, slot.path)]
            image_count += len(target_keys)
        else:
            warnings.append({"kind": "unsupported_type", "slot_id": slot_id, "type": slot.resource_type})
            skipped_count += 1
            continue
        for key in target_keys:
            previous = targets.get(key)
            if previous and previous != slot_id:
                errors.append({"kind": "duplicate_target", "slot_id": slot_id, "other_slot_id": previous, "target": list(key)})
            else:
                targets[key] = slot_id
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "enabled_changes": sum(1 for change in project.changes.values() if change.enabled),
        "value_targets": value_count,
        "image_targets": image_count,
        "skipped": skipped_count,
    }


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
    if catalog.source_path and _same_path(output, Path(catalog.source_path)):
        raise ValueError("不能覆盖资源目录对应的原始主题，请选择新的导出文件名")
    preflight = preflight_export(project, catalog)
    if not preflight["valid"]:
        raise ValueError("导出预检失败：" + json.dumps(preflight["errors"][:8], ensure_ascii=False))
    output.parent.mkdir(parents=True, exist_ok=True)
    slots = _slot_map(catalog, project)
    root_entries = blank_entries(project.title, project.author, project.designer, project.version, project.screen)
    module_files: dict[str, dict[str, bytes]] = defaultdict(dict)
    module_xml: dict[str, dict[str, dict[tuple[str, str], str]]] = defaultdict(lambda: defaultdict(dict))
    applied: list[dict] = []
    skipped: list[dict] = []

    for slot_id, change in project.changes.items():
        if not change.enabled:
            continue
        slot = slots.get(slot_id)
        if not slot:
            skipped.append({"slot_id": slot_id, "reason": "资源目录中不存在"})
            continue
        if slot.status == "当前版本不支持":
            skipped.append({"slot_id": slot_id, "reason": slot.status})
            continue
        if slot.resource_type in {"color", "bool", "integer", "dimen", "string"}:
            if change.value is None:
                skipped.append({"slot_id": slot_id, "reason": "没有设置值"})
                continue
            value = validate_change_value(slot.resource_type, change.value)
            module_xml[slot.module][slot.container][(slot.resource_type, slot.name)] = value
            applied.append({"slot_id": slot_id, "module": slot.module, "path": slot.container, "value": value})
            continue
        if slot.resource_type in {"image", "icon", "wallpaper", "preview"}:
            if change.source_kind == "placeholder":
                rendered = render_placeholder(slot)
            else:
                rendered = render_image(Path(change.source_file), slot, change)
            if slot.synthetic:
                for target in slot.targets:
                    module_files[target["module"]][target["path"]] = rendered
                    applied.append({"slot_id": slot_id, "module": target["module"], "path": target["path"]})
            elif slot.module == "__root__":
                root_entries[slot.path] = rendered
                applied.append({"slot_id": slot_id, "module": "__root__", "path": slot.path})
            else:
                module_files[slot.module][slot.path] = rendered
                applied.append({"slot_id": slot_id, "module": slot.module, "path": slot.path})

    temp = output.with_suffix(output.suffix + ".tmp")
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
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    digest_builder = hashlib.sha256()
    with output.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest_builder.update(block)
    digest = digest_builder.hexdigest()
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
    report_temp = report_path.with_suffix(report_path.suffix + ".tmp")
    try:
        report_temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(report_temp, report_path)
    except OSError as exc:
        report["report_warning"] = f"导出成功，但报告写入失败：{exc}"
    finally:
        report_temp.unlink(missing_ok=True)
    return output, report
