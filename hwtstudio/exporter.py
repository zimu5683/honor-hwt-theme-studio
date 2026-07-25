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
from .imageops import render_image
from .models import ResourceChange, ResourceSlot, ThemeCatalog, ThemeProject
from .validation import normalize_color, validate_theme


def safe_filename(value: str) -> str:
    invalid = '<>:"/\\|?*'
    result = "".join("_" if c in invalid else c for c in value).strip().strip(".")
    return result or "我的主题"


def default_export_name(project: ThemeProject) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"空白主题_{safe_filename(project.name)}_{timestamp}.hwt"


def _slot_map(catalog: ThemeCatalog, project: ThemeProject) -> dict[str, ResourceSlot]:
    return {slot.id: slot for slot in [*catalog.resources, *project.custom_resources]}


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
            value = change.value.strip()
            if slot.resource_type == "color":
                value = normalize_color(value)
            elif slot.resource_type == "bool":
                value = value.lower()
                if value not in {"true", "false"}:
                    raise ValueError(f"{slot.label} 的布尔值必须是 true 或 false")
            module_xml[slot.module][slot.container][(slot.resource_type, slot.name)] = value
            applied.append({"slot_id": slot_id, "module": slot.module, "path": slot.container, "value": value})
            continue
        if slot.resource_type in {"image", "icon", "wallpaper", "preview"}:
            if not change.source_file or not Path(change.source_file).is_file():
                skipped.append({"slot_id": slot_id, "reason": "图片文件不存在"})
                continue
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
    if temp.exists():
        temp.unlink()
    with ZipFile(temp, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in root_entries.items():
            archive.writestr(name, data)
        modules = sorted(set(module_files) | set(module_xml))
        for module in modules:
            data = _module_bytes(module_files[module], module_xml[module])
            archive.writestr(module, data)

    validation = validate_theme(temp)
    if not validation["valid"]:
        temp.unlink(missing_ok=True)
        raise ValueError("导出的主题未通过验证：" + json.dumps(validation["errors"][:5], ensure_ascii=False))
    os.replace(temp, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
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
    }
    report_path = output.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    project.dirty = False
    return output, report
