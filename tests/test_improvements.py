from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import struct
import tempfile
import threading
import unittest
import warnings
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image

from hwtstudio.blank import create_blank_theme
from hwtstudio.catalog import load_catalog, scan_theme
from hwtstudio.catalog import save_catalog
from hwtstudio.common import (
    MAX_ARCHIVE_ENTRIES,
    MAX_ARCHIVE_ENTRY_BYTES,
    MAX_ARCHIVE_COMPRESSION_RATIO,
    MAX_CATALOG_BYTES,
    MAX_PROJECT_BYTES,
    honor_module_name,
    honor_resource_name,
    honor_resource_path,
)
from hwtstudio.exporter import export_theme, preflight_export, safe_filename
from hwtstudio.models import ResourceChange, ResourceSlot, ThemeProject
from hwtstudio.models import ThemeCatalog
from hwtstudio.paths import bundled_catalog
from hwtstudio.pngmeta import extract_android_chunks, inject_android_chunks
from hwtstudio.projectio import load_project, project_assets_dir, save_project
from hwtstudio.phone_transfer import TransferCancelled
from hwtstudio.ssh_transfer import preflight_phone, transfer_to_phone
from hwtstudio.services.catalog_service import load_preferred_catalog
from hwtstudio.ui.dialogs import find_named_files
from hwtstudio.validation import validate_custom_slot, validate_theme
from hwtstudio.xmlutil import parse_xml


class ImprovementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(bundled_catalog())

    def test_export_refuses_to_overwrite_catalog_source_and_keeps_dirty(self):
        slot = next(item for item in self.catalog.resources if item.resource_type == "color")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.hwt"
            source.write_bytes(b"original")
            catalog = copy.deepcopy(self.catalog)
            catalog.source_path = str(source)
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=slot.id, value="#FF112233"))
            with self.assertRaisesRegex(ValueError, "不能覆盖"):
                export_theme(project, catalog, source)
            self.assertEqual(source.read_bytes(), b"original")
            output = Path(directory) / "output.hwt"
            export_theme(project, catalog, output)
            self.assertTrue(project.dirty)

    def test_export_filename_sanitizes_control_text_and_length(self):
        safe = safe_filename("主题\n" + "很长" * 100)
        self.assertNotIn("\n", safe)
        self.assertLessEqual(len(safe), 80)
        self.assertFalse(safe_filename("a" * 79 + ".suffix").endswith("."))
        self.assertFalse(safe_filename("a" * 79 + " suffix").endswith(" "))

    def test_report_write_failure_does_not_remove_export(self):
        slot = next(item for item in self.catalog.resources if item.resource_type == "color")
        project = ThemeProject()
        project.set_change(ResourceChange(slot_id=slot.id, value="#FF112233"))
        original_write_text = Path.write_text

        def guarded_write_text(path, *args, **kwargs):
            if ".report.json." in path.name and path.name.endswith(".tmp"):
                raise OSError("read only report directory")
            return original_write_text(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch.object(Path, "write_text", guarded_write_text):
            output = Path(directory) / "output.hwt"
            _, report = export_theme(project, self.catalog, output)
            self.assertTrue(output.is_file())
            self.assertIn("报告写入失败", report["report_warning"])

    def test_export_rejects_report_filename_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "theme.report.json"
            with self.assertRaisesRegex(ValueError, r"不能以 \.report\.json 结尾"):
                export_theme(ThemeProject(), self.catalog, output)
            self.assertFalse(output.exists())

    def test_report_serialization_failure_keeps_export(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "theme.hwt"
            catalog = copy.deepcopy(self.catalog)
            catalog.source_sha256 = object()
            _, report = export_theme(ThemeProject(), catalog, output)
            self.assertTrue(output.is_file())
            self.assertIn("报告写入失败", report["report_warning"])

    def test_schema_1_migrates_and_assets_move_with_project(self):
        slot = next(item for item in self.catalog.resources if item.resource_type == "wallpaper")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "原图.jpg"
            Image.new("RGB", (30, 40), (10, 20, 30)).save(image)
            old_file = root / "legacy.hwtproj.json"
            old_file.write_text(json.dumps({
                "schema": 1,
                "changes": {slot.id: {"slot_id": slot.id, "source_file": str(image)}},
            }, ensure_ascii=False), encoding="utf-8")
            project = load_project(old_file)
            save_project(project, old_file)
            raw = json.loads(old_file.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], 2)
            stored = raw["changes"][slot.id]["source_file"]
            self.assertFalse(Path(stored).is_absolute())
            self.assertIn("root", Path(stored).name)
            self.assertIn("原图.jpg", Path(stored).name)
            first_assets = sorted(path.name for path in project_assets_dir(old_file).iterdir())
            save_project(project, old_file)
            self.assertEqual(first_assets, sorted(path.name for path in project_assets_dir(old_file).iterdir()))

            moved = root / "moved"
            moved.mkdir()
            moved_project = moved / old_file.name
            shutil.copy2(old_file, moved_project)
            shutil.copytree(project_assets_dir(old_file), project_assets_dir(moved_project))
            loaded = load_project(moved_project)
            self.assertTrue(Path(loaded.changes[slot.id].source_file).is_file())
            export_theme(loaded, self.catalog, moved / "moved.hwt")

    def test_project_loader_rejects_oversized_and_malformed_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            oversized = root / "oversized.hwtproj.json"
            oversized.write_bytes(b" " * (MAX_PROJECT_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "超过允许的大小"):
                load_project(oversized)

            malformed = root / "malformed.hwtproj.json"
            malformed.write_text(json.dumps({"schema": 2, "changes": []}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changes"):
                load_project(malformed)

    def test_project_save_rejects_oversized_json_before_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "oversized.hwtproj.json"
            project = ThemeProject(name="x" * (MAX_PROJECT_BYTES + 1))

            with self.assertRaisesRegex(ValueError, "超过允许的大小"):
                save_project(project, target)

            self.assertFalse(target.exists())
            self.assertFalse(project_assets_dir(target).exists())
            self.assertEqual(list(root.glob(".*")), [])
            self.assertIsNone(project.project_file)

    def test_project_save_write_failure_does_not_commit_assets(self):
        slot = next(item for item in self.catalog.resources if item.resource_type == "wallpaper")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wallpaper.png"
            Image.new("RGB", (8, 8), (20, 30, 40)).save(source)
            target = root / "theme.hwtproj.json"
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=slot.id, source_file=str(source)))
            original_write_bytes = Path.write_bytes

            def fail_project_write(path, data):
                if path.name.startswith(f".{target.name}.") and path.name.endswith(".tmp"):
                    raise OSError("工程文件不可写")
                return original_write_bytes(path, data)

            with patch.object(Path, "write_bytes", fail_project_write):
                with self.assertRaisesRegex(OSError, "不可写"):
                    save_project(project, target)
            self.assertFalse(target.exists())
            self.assertFalse(project_assets_dir(target).exists())
            self.assertEqual(list(root.glob(".*.tmp")), [])
            self.assertTrue(project.dirty)
            self.assertIsNone(project.project_file)

    def test_project_save_commit_failure_restores_project_and_assets(self):
        slot = next(item for item in self.catalog.resources if item.resource_type == "wallpaper")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.png"
            second = root / "second.png"
            Image.new("RGB", (8, 8), (20, 30, 40)).save(first)
            Image.new("RGB", (8, 8), (90, 80, 70)).save(second)
            target = root / "theme.hwtproj.json"
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=slot.id, source_file=str(first)))
            save_project(project, target)
            old_json = target.read_bytes()
            old_asset = next(project_assets_dir(target).iterdir()).read_bytes()
            project.set_change(ResourceChange(slot_id=slot.id, source_file=str(second)))
            original_replace = os.replace

            def fail_new_project(source, destination):
                if (
                    Path(destination) == target
                    and Path(source).name.startswith(f".{target.name}.")
                    and Path(source).name.endswith(".tmp")
                ):
                    raise OSError("工程提交失败")
                return original_replace(source, destination)

            with patch("hwtstudio.projectio.os.replace", side_effect=fail_new_project):
                with self.assertRaisesRegex(OSError, "提交失败"):
                    save_project(project, target)
            self.assertEqual(target.read_bytes(), old_json)
            self.assertEqual(next(project_assets_dir(target).iterdir()).read_bytes(), old_asset)
            self.assertTrue(project.dirty)

    def test_project_save_removes_unreferenced_assets_but_keeps_disabled_assets(self):
        slot = next(item for item in self.catalog.resources if item.resource_type == "wallpaper")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wallpaper.png"
            Image.new("RGB", (8, 8), (20, 30, 40)).save(source)
            target = root / "theme.hwtproj.json"
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=slot.id, source_file=str(source)))
            save_project(project, target)
            asset_dir = project_assets_dir(target)
            self.assertTrue(asset_dir.is_dir())

            project.changes[slot.id].enabled = False
            project.dirty = True
            save_project(project, target)
            self.assertTrue(asset_dir.is_dir())
            self.assertEqual(len(list(asset_dir.iterdir())), 1)

            project.remove_change(slot.id)
            save_project(project, target)
            self.assertFalse(asset_dir.exists())

    def test_placeholder_exports_without_source_file(self):
        slot = next(item for item in self.catalog.resources if item.resource_type == "wallpaper")
        project = ThemeProject()
        project.set_change(ResourceChange(slot_id=slot.id, source_kind="placeholder"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "placeholder.hwt"
            export_theme(project, self.catalog, output)
            with ZipFile(output) as archive:
                with Image.open(BytesIO(archive.read(slot.path))) as image:
                    self.assertEqual(image.size, (slot.width, slot.height))

    def test_icon_export_replaces_compatibility_module_without_duplicate(self):
        slot = next(item for item in self.catalog.resources if item.resource_type == "icon")
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "icon.png"
            Image.new("RGBA", (64, 64), (20, 40, 60, 255)).save(image)
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=slot.id, source_file=str(image)))
            output = Path(directory) / "icon.hwt"
            export_theme(project, self.catalog, output)
            with ZipFile(output) as outer:
                self.assertEqual(outer.namelist().count("icons"), 1)
                with ZipFile(BytesIO(outer.read("icons"))) as icons:
                    self.assertIn(slot.path, icons.namelist())

    def test_huawei_source_resources_export_to_honor_targets(self):
        catalog = copy.deepcopy(self.catalog)
        color_slot = ResourceSlot(
            id="__source__::huawei::color",
            module="com.huawei.android.launcher",
            container="theme.xml",
            resource_type="color",
            name="emui_color_bg",
            path="theme.xml",
            category="桌面",
            label="华为颜色",
        )
        image_slot = ResourceSlot(
            id="__source__::huawei::image",
            module="com.huawei.android.launcher",
            container="",
            resource_type="image",
            name="emui_panel.png",
            path="framework-res-hwext/res/drawable/emui_panel.png",
            category="桌面",
            label="华为图片",
            width=8,
            height=8,
        )
        catalog.resources.extend([color_slot, image_slot])
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "panel.png"
            Image.new("RGBA", (8, 8), (20, 40, 60, 255)).save(source)
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=color_slot.id, value="#FF112233"))
            project.set_change(ResourceChange(slot_id=image_slot.id, source_file=str(source)))
            output = Path(directory) / "converted.hwt"
            export_theme(project, catalog, output)

            with ZipFile(output) as outer:
                self.assertIn("com.hihonor.android.launcher", outer.namelist())
                self.assertNotIn("com.huawei.android.launcher", outer.namelist())
                with ZipFile(BytesIO(outer.read("com.hihonor.android.launcher"))) as module:
                    self.assertIn("framework-res-hnext/res/drawable/magic_panel.png", module.namelist())
                    xml = module.read("theme.xml").decode("utf-8")
                    self.assertIn('name="magic_color_bg"', xml)

    def test_huawei_conversion_mapping_covers_confirmed_aliases(self):
        self.assertEqual(honor_module_name("com.huawei.phone.recorder"), "com.hihonor.phone.recorder")
        self.assertEqual(honor_module_name("com.huawei.aod"), "com.hihonor.aod")
        self.assertEqual(honor_resource_name("hwtoolbar_background"), "hntoolbar_background")
        self.assertEqual(
            honor_resource_path("framework-res-hwext/res/drawable/emui_status.png"),
            "framework-res-hnext/res/drawable/magic_status.png",
        )
        self.assertEqual(
            honor_resource_path(
                "dynamic_icons/com.huawei.android.totemweather/com.huawei.weather.png",
            ),
            "dynamic_icons/com.hihonor.android.totemweather/com.hihonor.weather.png",
        )
        self.assertEqual(
            honor_resource_path("dynamic_icons/com.huawei.music.png"),
            "dynamic_icons/com.google.android.apps.youtube.music.png",
        )
        self.assertEqual(
            honor_resource_path("dynamic_icons/com.android.deskclock/clock.png"),
            "dynamic_icons/com.hihonor.deskclock/clock.png",
        )

    def test_mapped_targets_with_same_content_are_merged(self):
        native = ResourceSlot(
            id="__native__::color",
            module="framework-res-hnext",
            container="theme.xml",
            resource_type="color",
            name="magic_color_bg",
            path="theme.xml",
            category="荣耀系统框架",
            label="荣耀页面背景",
        )
        source = ResourceSlot(
            id="__source__::color",
            module="framework-res-hwext",
            container="theme.xml",
            resource_type="color",
            name="emui_color_bg",
            path="theme.xml",
            category="华为系统框架",
            label="华为页面背景",
        )
        catalog = ThemeCatalog("", "", "", {}, [], [native, source])
        project = ThemeProject()
        project.set_change(ResourceChange(slot_id=native.id, value="#FF112233"))
        project.set_change(ResourceChange(slot_id=source.id, value="#FF112233"))

        result = preflight_export(project, catalog)

        self.assertTrue(result["valid"])
        self.assertEqual(result["value_targets"], 1)
        merged = [item for item in result["warnings"] if item["kind"] == "duplicate_target_merged"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["selected_slot_id"], native.id)

    def test_mapped_targets_with_different_content_prefer_native_honor_resource(self):
        native = ResourceSlot(
            id="__native__::color",
            module="framework-res-hnext",
            container="theme.xml",
            resource_type="color",
            name="magic_color_bg",
            path="theme.xml",
            category="荣耀系统框架",
            label="荣耀页面背景",
        )
        source = ResourceSlot(
            id="__source__::color",
            module="framework-res-hwext",
            container="theme.xml",
            resource_type="color",
            name="emui_color_bg",
            path="theme.xml",
            category="华为系统框架",
            label="华为页面背景",
        )
        catalog = ThemeCatalog("", "", "", {}, [], [native, source])
        project = ThemeProject()
        project.set_change(ResourceChange(slot_id=source.id, value="#FF000000"))
        project.set_change(ResourceChange(slot_id=native.id, value="#FF112233"))

        result = preflight_export(project, catalog)

        self.assertTrue(result["valid"])
        resolved = [item for item in result["warnings"] if item["kind"] == "duplicate_target_resolved"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["selected_slot_id"], native.id)
        self.assertIn(source.id, resolved[0]["discarded_slot_ids"])

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "native-wins.hwt"
            _, report = export_theme(project, catalog, output)
            with ZipFile(output) as outer:
                with ZipFile(BytesIO(outer.read("framework-res-hnext"))) as module:
                    xml = module.read("theme.xml").decode("utf-8")
            self.assertIn("#FF112233", xml)
            self.assertNotIn("#FF000000", xml)
            self.assertEqual(report["preflight"]["warnings"], result["warnings"])

    def test_custom_slot_and_duplicate_target_validation(self):
        invalid = ResourceSlot(
            id="__custom__::bad", module="description.xml", container="../theme.xml", resource_type="color",
            name="x", path="../theme.xml", category="高级自定义", label="bad",
        )
        with self.assertRaises(ValueError):
            validate_custom_slot(invalid)

        first = ResourceSlot(
            id="__custom__::one", module="com.example", container="theme.xml", resource_type="color",
            name="same", path="theme.xml", category="高级自定义", label="one",
        )
        second = copy.deepcopy(first)
        second.id = "__custom__::two"
        project = ThemeProject(custom_resources=[first, second])
        project.set_change(ResourceChange(slot_id=first.id, value="#FFFFFFFF"))
        project.set_change(ResourceChange(slot_id=second.id, value="#FF000000"))
        result = preflight_export(project, self.catalog)
        self.assertFalse(result["valid"])
        self.assertTrue(any(item["kind"] == "duplicate_target" for item in result["errors"]))

    def test_validator_rejects_duplicate_and_malformed_nested_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "invalid.hwt"
            create_blank_theme(output)
            module_data = BytesIO()
            with ZipFile(module_data, "w", ZIP_DEFLATED) as module:
                module.writestr("theme.xml", b"<resources><integer name='bad'>1.5</integer></resources>")
                module.writestr("fake.png", b"not a png")
                module.writestr("../escape.xml", b"<resources/>")
            with ZipFile(output, "a", ZIP_DEFLATED) as outer:
                outer.writestr("com.example", module_data.getvalue())
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    outer.writestr("description.xml", b"<HwTheme/>")
            result = validate_theme(output)
            kinds = {item["kind"] for item in result["errors"]}
            self.assertIn("duplicate_zip_entry", kinds)
            self.assertIn("integer", kinds)
            self.assertIn("image_format", kinds)
        self.assertIn("unsafe_nested_path", kinds)

    def test_archive_path_overlaps_are_blocked_at_both_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "overlap.hwt"
            create_blank_theme(output)
            nested_data = BytesIO()
            with ZipFile(nested_data, "w", ZIP_DEFLATED) as nested:
                nested.writestr("theme.xml", b"<resources><color name='safe'>#FFFFFFFF</color></resources>")
                nested.writestr("theme.xml/extra.png", b"not an image")
                nested.writestr("theme.xml/assets/", b"")
            with ZipFile(output, "a", ZIP_DEFLATED) as outer:
                outer.writestr("com.example", nested_data.getvalue())
                outer.writestr("icons/theme.png", b"not an image")

            catalog = scan_theme(output)
            result = validate_theme(output)

        warning_kinds = {item["kind"] for item in catalog.warnings}
        self.assertIn("path_overlap", warning_kinds)
        self.assertIn("nested_path_overlap", warning_kinds)
        kinds = {item["kind"] for item in result["errors"]}
        self.assertIn("path_overlap", kinds)

        with tempfile.TemporaryDirectory() as directory:
            nested_only = Path(directory) / "nested-overlap.hwt"
            create_blank_theme(nested_only)
            with ZipFile(nested_only, "a", ZIP_DEFLATED) as outer:
                outer.writestr("com.example", nested_data.getvalue())
            nested_result = validate_theme(nested_only)
        self.assertIn("nested_path_overlap", {item["kind"] for item in nested_result["errors"]})

    def test_validator_rejects_oversized_entry_before_decompression(self):
        info = MagicMock()
        info.filename = "bomb.bin"
        info.file_size = MAX_ARCHIVE_ENTRY_BYTES + 1
        info.is_dir.return_value = False
        outer = MagicMock()
        outer.__enter__.return_value = outer
        outer.__exit__.return_value = False
        outer.infolist.return_value = [info]
        outer.namelist.return_value = [info.filename]
        with patch("hwtstudio.validation.ZipFile", return_value=outer):
            result = validate_theme(Path("bomb.hwt"))
        self.assertFalse(result["valid"])
        self.assertIn("oversized_entry", {item["kind"] for item in result["errors"]})
        outer.testzip.assert_not_called()

    def test_validator_rejects_excessive_zip_entry_count_before_reading(self):
        infos = []
        for index in range(MAX_ARCHIVE_ENTRIES + 1):
            info = MagicMock()
            info.filename = f"entry-{index}"
            info.file_size = 0
            info.is_dir.return_value = False
            infos.append(info)
        outer = MagicMock()
        outer.__enter__.return_value = outer
        outer.__exit__.return_value = False
        outer.infolist.return_value = infos
        outer.namelist.return_value = [info.filename for info in infos]
        with patch("hwtstudio.validation.ZipFile", return_value=outer):
            result = validate_theme(Path("many-entries.hwt"))
        self.assertFalse(result["valid"])
        self.assertIn("too_many_entries", {item["kind"] for item in result["errors"]})
        outer.testzip.assert_not_called()
        outer.read.assert_not_called()

    def test_validator_rejects_archive_bomb_and_symlink_entries_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unsafe-archive.hwt"
            create_blank_theme(output)
            module_data = BytesIO()
            with ZipFile(module_data, "w", ZIP_DEFLATED) as module:
                module.writestr("bomb.bin", b"\0" * (2 * 1024 * 1024))
                link = ZipInfo("link")
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                module.writestr(link, b"../escape.txt")
            with ZipFile(output, "a", ZIP_DEFLATED) as outer:
                outer.writestr("com.example", module_data.getvalue())
            result = validate_theme(output)
        kinds = {item["kind"] for item in result["errors"]}
        self.assertIn("nested_compression_ratio", kinds)
        self.assertIn("nested_symlink_entry", kinds)
        self.assertGreater(
            next(item["ratio"] for item in result["errors"] if item["kind"] == "nested_compression_ratio"),
            MAX_ARCHIVE_COMPRESSION_RATIO,
        )

    def test_validator_rejects_nfc_collisions_and_zip64_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nfc-archive.hwt"
            create_blank_theme(output)
            module_data = BytesIO()
            with ZipFile(module_data, "w", ZIP_DEFLATED) as module:
                module.writestr("e\u0301.txt", b"one")
                module.writestr("\u00e9.txt", b"two")
                bad = ZipInfo("zip64.bin")
                bad.extra = struct.pack("<HHQQ", 0x0001, 16, 4, 99)
                module.writestr(bad, b"data")
            with ZipFile(output, "a", ZIP_DEFLATED) as outer:
                outer.writestr("com.example", module_data.getvalue())
            result = validate_theme(output)
        kinds = {item["kind"] for item in result["errors"]}
        self.assertIn("duplicate_normalized_nested_entry", kinds)
        self.assertIn("nested_zip64_inconsistent", kinds)

    def test_validator_skips_unsafe_paths_before_reading_content(self):
        info = MagicMock()
        info.filename = "../escape.zip"
        info.file_size = 32
        info.is_dir.return_value = False
        outer = MagicMock()
        outer.__enter__.return_value = outer
        outer.__exit__.return_value = False
        outer.infolist.return_value = [info]
        outer.namelist.return_value = [info.filename]
        with patch("hwtstudio.validation.ZipFile", return_value=outer):
            result = validate_theme(Path("unsafe.hwt"))
        self.assertFalse(result["valid"])
        self.assertIn("unsafe_path", {item["kind"] for item in result["errors"]})
        outer.read.assert_not_called()
        outer.testzip.assert_not_called()

    def test_validator_reports_entry_read_failure_without_raising(self):
        info = MagicMock()
        info.filename = "broken.bin"
        info.file_size = 1
        info.is_dir.return_value = False
        outer = MagicMock()
        outer.__enter__.return_value = outer
        outer.__exit__.return_value = False
        outer.infolist.return_value = [info]
        outer.namelist.return_value = [info.filename]
        outer.testzip.return_value = None
        outer.read.side_effect = OSError("压缩流读取失败")
        with patch("hwtstudio.validation.ZipFile", return_value=outer):
            result = validate_theme(Path("broken.hwt"))
        self.assertFalse(result["valid"])
        read_error = next(item for item in result["errors"] if item["kind"] == "entry_read")
        self.assertEqual(read_error["path"], "broken.bin")

    def test_xml_parser_does_not_expand_external_entities(self):
        raw = b'''<!DOCTYPE resources [<!ENTITY external SYSTEM "file:///definitely-not-read.txt">]>
            <resources><string name="value">&external;</string></resources>'''
        root = parse_xml(raw)
        node = root.find("string")
        self.assertIsNotNone(node)
        self.assertIsNone(node.text)

    def test_ninepatch_chunk_round_trip(self):
        raw = BytesIO()
        Image.new("RGBA", (4, 4), (1, 2, 3, 255)).save(raw, "PNG")
        injected = inject_android_chunks(raw.getvalue(), {"npTc": "AAECAw=="})
        self.assertEqual(extract_android_chunks(injected)["npTc"], "AAECAw==")

    def test_recursive_missing_asset_search_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "Photo.PNG"
            target.parent.mkdir()
            target.write_bytes(b"x")
            self.assertEqual(find_named_files(Path(directory), "photo.png"), [target])

    def test_preferred_catalog_uses_valid_cache_and_falls_back_when_corrupt(self):
        slot = copy.deepcopy(self.catalog.resources[0])
        cached_catalog = ThemeCatalog("cached.hwt", "abc", "now", {"resource_slots": 1}, [], [slot])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_catalog(cached_catalog, root / "catalog_daxue.json")
            with patch("hwtstudio.services.catalog_service.data_dir", return_value=root):
                loaded, warning = load_preferred_catalog()
                self.assertEqual(loaded.source_path, "cached.hwt")
                self.assertEqual(warning, "")
                (root / "catalog_daxue.json").write_text("broken", encoding="utf-8")
                loaded, warning = load_preferred_catalog()
                self.assertNotEqual(loaded.source_path, "cached.hwt")
                self.assertIn("已回退", warning)

    def test_catalog_loader_bounds_and_validates_cache_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            oversized = root / "oversized-catalog.json"
            oversized.write_bytes(b" " * (MAX_CATALOG_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "超过允许的大小"):
                load_catalog(oversized)

            malformed = root / "malformed-catalog.json"
            malformed.write_text(json.dumps({"schema": 1, "resources": [{}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "资源记录格式无效"):
                load_catalog(malformed)

            wrong_types = json.loads(json.dumps(self.catalog.to_dict(), ensure_ascii=False))
            wrong_types["resources"][0]["id"] = []
            malformed.write_text(json.dumps(wrong_types, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "文字字段类型无效"):
                load_catalog(malformed)

            wrong_optional = json.loads(json.dumps(self.catalog.to_dict(), ensure_ascii=False))
            wrong_optional["resources"][0]["occurrences"] = "many"
            malformed.write_text(json.dumps(wrong_optional, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "occurrences"):
                load_catalog(malformed)

            unsafe_path = json.loads(json.dumps(self.catalog.to_dict(), ensure_ascii=False))
            unsafe_path["resources"][0]["path"] = "../outside.png"
            malformed.write_text(json.dumps(unsafe_path, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "路径不安全"):
                load_catalog(malformed)

            duplicate_ids = json.loads(json.dumps(self.catalog.to_dict(), ensure_ascii=False))
            duplicate_ids["resources"][1]["id"] = duplicate_ids["resources"][0]["id"]
            malformed.write_text(json.dumps(duplicate_ids, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ID 重复"):
                load_catalog(malformed)

            invalid_stats = json.loads(json.dumps(self.catalog.to_dict(), ensure_ascii=False))
            invalid_stats["stats"]["resource_slots"] = -1
            malformed.write_text(json.dumps(invalid_stats, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "统计字段"):
                load_catalog(malformed)

            oversized_catalog = ThemeCatalog(
                source_path="x" * (MAX_CATALOG_BYTES + 1),
                source_sha256="",
                generated_at="",
                stats={},
                warnings=[],
                resources=[],
            )
            saved = root / "oversized-saved-catalog.json"
            with self.assertRaisesRegex(ValueError, "保存的资源目录文件超过"):
                save_catalog(oversized_catalog, saved)
            self.assertFalse(saved.exists())
            self.assertEqual(list(root.glob(".*.tmp")), [])

    @patch("hwtstudio.ssh_transfer._run")
    @patch("hwtstudio.ssh_transfer.shutil.which", return_value="C:/Windows/System32/OpenSSH/tool.exe")
    def test_phone_preflight_reports_optional_am(self, _which, run):
        def result(stdout="", returncode=0, stderr=""):
            return type("Result", (), {"stdout": stdout, "stderr": stderr, "returncode": returncode})()
        run.side_effect = [result("ready"), result(), result("hash_ok")]
        report = preflight_phone()
        self.assertTrue(report["valid"])
        self.assertTrue(report["warnings"])

    @patch("hwtstudio.ssh_transfer.shutil.which", return_value=None)
    def test_phone_preflight_reports_missing_openssh(self, _which):
        report = preflight_phone()
        self.assertFalse(report["valid"])
        self.assertEqual(len(report["errors"]), 2)

    def test_ssh_transfer_refuses_file_changed_after_hashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.hwt"
            path.write_bytes(b"original")
            before = path.stat()

            def hash_then_mutate(_path, *, cancelled=None):
                path.write_bytes(b"changed")
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
                return "0" * 64

            with patch("hwtstudio.ssh_transfer.local_sha256", side_effect=hash_then_mutate):
                with self.assertRaisesRegex(RuntimeError, "校验后"):
                    transfer_to_phone(path)

    def test_ssh_transfer_cleans_remote_temp_after_upload_failure(self):
        def result(returncode=0, stdout="", stderr=""):
            return type("Result", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.hwt"
            path.write_bytes(b"payload")
            with patch(
                "hwtstudio.ssh_transfer.preflight_phone",
                return_value={"valid": True, "checks": [], "errors": [], "warnings": []},
            ), patch("hwtstudio.ssh_transfer._run_with_cancel") as run:
                run.side_effect = [result(), result(returncode=1, stderr="scp failed"), result()]
                with self.assertRaisesRegex(RuntimeError, "上传失败"):
                    transfer_to_phone(path)
                self.assertEqual(run.call_count, 3)
                cleanup_command = run.call_args_list[-1].args[0]
                self.assertIn("rm -f", cleanup_command[2])

    def test_ssh_transfer_cleans_remote_temp_after_cancellation(self):
        def result(returncode=0, stdout="", stderr=""):
            return type("Result", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.hwt"
            path.write_bytes(b"payload")
            cancelled = threading.Event()
            calls = []

            def run_command(args, *, cancelled=None, **_kwargs):
                if len(calls) == 0:
                    calls.append(args)
                    return result()
                if len(calls) == 1:
                    calls.append(args)
                    cancelled_event = cancellation_event
                    cancelled_event.set()
                    raise TransferCancelled()
                calls.append(args)
                return result()

            cancellation_event = cancelled
            with patch(
                "hwtstudio.ssh_transfer.preflight_phone",
                return_value={"valid": True, "checks": [], "errors": [], "warnings": []},
            ), patch("hwtstudio.ssh_transfer._run_with_cancel", side_effect=run_command):
                with self.assertRaises(TransferCancelled):
                    transfer_to_phone(path, cancelled=cancelled)

            self.assertEqual(len(calls), 3)
            self.assertIn("rm -f", calls[-1][2])


if __name__ == "__main__":
    unittest.main()
