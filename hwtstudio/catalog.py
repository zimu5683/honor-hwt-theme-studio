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

from .common import COMMON_BACKGROUND_TARGETS, friendly_label, module_category, risk_for
from .models import ResourceSlot, ThemeCatalog
from .pngmeta import extract_android_chunks


VALUE_PATTERN = re.compile(
    rb"<(color|bool|integer|dimen|string)\s+[^>]*name\s*=\s*['\"]([^'\"]+)['\"][^>]*>(.*?)</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


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
        from lxml import etree

        etree.fromstring(raw)
    except Exception as exc:
        warnings.append({"kind": "nonstandard_xml", "module": module, "path": container, "message": str(exc)})
    return resources


def _image_slot(module: str, path: str, raw: bytes, resource_type: str = "image") -> ResourceSlot:
    width = height = None
    mode = None
    try:
        with Image.open(BytesIO(raw)) as image:
            width, height, mode = image.width, image.height, image.mode
    except Exception:
        pass
    name = Path(path).name
    chunks = extract_android_chunks(raw) if detect_format(raw) == "PNG" else {}
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
        actual_format=detect_format(raw),
        extension=Path(path).suffix.lower(),
        ninepatch="npTc" in chunks,
        png_chunks=chunks,
    )


def _scan_module(module: str, raw: bytes, warnings: list[dict]) -> tuple[list[ResourceSlot], dict[str, int]]:
    resources: list[ResourceSlot] = []
    stats = Counter()
    try:
        with ZipFile(BytesIO(raw)) as archive:
            bad = archive.testzip()
            if bad:
                warnings.append({"kind": "module_crc", "module": module, "path": bad})
            for info in archive.infolist():
                if info.is_dir():
                    continue
                path = info.filename
                data = archive.read(info)
                if path.endswith("theme.xml"):
                    slots = _scan_xml(module, path, data, warnings)
                    resources.extend(slots)
                    for slot in slots:
                        stats[f"{slot.resource_type}_slots"] += 1
                        stats[f"{slot.resource_type}_declarations"] += slot.occurrences
                elif Path(path).suffix.lower() in IMAGE_EXTENSIONS:
                    kind = "icon" if module == "icons" else "image"
                    resources.append(_image_slot(module, path, data, kind))
                    stats[f"{kind}_slots"] += 1
    except BadZipFile:
        warnings.append({"kind": "bad_module_zip", "module": module})
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
        bad = outer.testzip()
        if bad:
            warnings.append({"kind": "outer_crc", "path": bad})
        for info in outer.infolist():
            if info.is_dir():
                continue
            name = info.filename
            raw = outer.read(info)
            if name in {"description.xml", "unlock/theme.xml"}:
                continue
            if name.startswith("wallpaper/") and Path(name).suffix.lower() in IMAGE_EXTENSIONS:
                resources.append(_image_slot("__root__", name, raw, "wallpaper"))
                stats["wallpaper_slots"] += 1
                continue
            if name.startswith("preview/") and Path(name).suffix.lower() in IMAGE_EXTENSIONS:
                resources.append(_image_slot("__root__", name, raw, "preview"))
                stats["preview_slots"] += 1
                continue
            try:
                with ZipFile(BytesIO(raw)):
                    pass
            except BadZipFile:
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


def save_catalog(catalog: ThemeCatalog, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        temp.write_text(json.dumps(catalog.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load_catalog(path: Path) -> ThemeCatalog:
    return ThemeCatalog.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
