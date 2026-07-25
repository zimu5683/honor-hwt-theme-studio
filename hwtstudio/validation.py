from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml import etree

from .catalog import detect_format


COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


def normalize_color(value: str) -> str:
    value = value.strip()
    if not value.startswith("#"):
        value = "#" + value
    if len(value) not in {7, 9} or not COLOR_RE.fullmatch(value):
        raise ValueError("颜色必须是 #RRGGBB 或 #AARRGGBB")
    # HWT files conventionally use #AARRGGBB; retain explicitly supplied RGB values.
    return value.upper()


def validate_theme(path: Path) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    modules = 0
    resources = 0
    try:
        with ZipFile(path) as outer:
            bad = outer.testzip()
            if bad:
                errors.append({"kind": "crc", "path": bad})
            names = set(outer.namelist())
            required = {"description.xml", "unlock/theme.xml", "wallpaper/home_wallpaper_0.jpg", "wallpaper/unlock_wallpaper_0.jpg"}
            for missing in sorted(required - names):
                errors.append({"kind": "missing_required", "path": missing})
            for info in outer.infolist():
                if info.is_dir():
                    continue
                raw = outer.read(info)
                if info.filename.endswith(".xml"):
                    try:
                        etree.fromstring(raw)
                    except Exception as exc:
                        errors.append({"kind": "xml", "path": info.filename, "message": str(exc)})
                try:
                    with ZipFile(BytesIO(raw)) as module:
                        modules += 1
                        nested_bad = module.testzip()
                        if nested_bad:
                            errors.append({"kind": "nested_crc", "module": info.filename, "path": nested_bad})
                        for child in module.infolist():
                            if child.is_dir():
                                continue
                            child_raw = module.read(child)
                            if child.filename.endswith("theme.xml"):
                                try:
                                    root = etree.fromstring(child_raw)
                                    resources += len(root)
                                    seen = set()
                                    for node in root:
                                        key = (node.tag, node.get("name"))
                                        if key in seen:
                                            errors.append({"kind": "duplicate", "module": info.filename, "path": child.filename, "name": key[1]})
                                        seen.add(key)
                                        if node.tag == "color":
                                            try:
                                                normalize_color(node.text or "")
                                            except ValueError as exc:
                                                errors.append({"kind": "color", "module": info.filename, "path": child.filename, "name": key[1], "message": str(exc)})
                                        elif node.tag == "bool" and (node.text or "").strip().lower() not in {"true", "false"}:
                                            errors.append({"kind": "bool", "module": info.filename, "path": child.filename, "name": key[1]})
                                except Exception as exc:
                                    errors.append({"kind": "nested_xml", "module": info.filename, "path": child.filename, "message": str(exc)})
                            suffix = Path(child.filename).suffix.lower()
                            if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
                                fmt = detect_format(child_raw)
                                expected = "PNG" if suffix == ".png" else "JPEG" if suffix in {".jpg", ".jpeg"} else "WEBP"
                                if fmt != expected:
                                    errors.append({"kind": "image_format", "module": info.filename, "path": child.filename, "expected": expected, "actual": fmt})
                except BadZipFile:
                    pass
    except BadZipFile as exc:
        errors.append({"kind": "outer_zip", "message": str(exc)})
    return {"valid": not errors, "errors": errors, "warnings": warnings, "modules": modules, "resource_nodes": resources}

