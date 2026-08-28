from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import stat
import struct
import tempfile
import threading
import time
import unittest
import warnings
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from PIL import Image

from hwtstudio.archive_safety import archive_data_overlaps
from hwtstudio.blank import create_blank_theme
from hwtstudio.catalog import load_catalog, save_catalog, save_source_compatibility_report, scan_theme
from hwtstudio.common import (
    MAX_ARCHIVE_COMPRESSION_RATIO,
    MAX_ARCHIVE_ENTRIES,
    MAX_ARCHIVE_ENTRY_BYTES,
    MAX_CATALOG_BYTES,
    MAX_PROJECT_BYTES,
    honor_module_name,
    honor_resource_name,
    honor_resource_path,
    honor_resource_paths,
    is_safe_archive_path,
)
from hwtstudio.exporter import default_export_name, export_theme, preflight_export, safe_filename
from hwtstudio.imageops import render_image as render_source_image
from hwtstudio.locking import InterprocessLockTimeoutError
from hwtstudio.models import ResourceChange, ResourceSlot, ThemeCatalog, ThemeProject
from hwtstudio.paths import bundled_catalog, unique_temp_path
from hwtstudio.phone_transfer import TransferCancelled
from hwtstudio.pngmeta import extract_android_chunks, inject_android_chunks
from hwtstudio.projectio import load_project, project_assets_dir, save_project
from hwtstudio.services import catalog_service
from hwtstudio.services.catalog_service import load_preferred_catalog, save_user_catalog
from hwtstudio.services.project_assets import asset_name, collect_project_assets
from hwtstudio.ssh_transfer import REMOTE_DIR, preflight_phone, transfer_to_phone
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

    def test_default_export_name_prefers_custom_title_then_project_name(self):
        with patch("hwtstudio.exporter.datetime") as mocked_datetime:
            now = mocked_datetime.now.return_value
            now.astimezone.return_value.strftime.return_value = "20260814_120000"
            self.assertEqual(
                default_export_name(ThemeProject(name="短信工程", title="短信主题")),
                "短信主题_20260814_120000.hwt",
            )
            self.assertEqual(
                default_export_name(ThemeProject(name="短信工程")),
                "短信工程_20260814_120000.hwt",
            )
            self.assertEqual(
                default_export_name(ThemeProject()),
                "空白主题_20260814_120000.hwt",
            )

    def test_default_metadata_title_uses_export_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "短信 & 通知.hwt"
            _, report = export_theme(ThemeProject(), self.catalog, output)
            with ZipFile(output) as archive:
                root = parse_xml(archive.read("description.xml"))
            self.assertEqual(root.findtext("title"), "短信 & 通知")
            self.assertEqual(root.findtext("title-cn"), "短信 & 通知")
            self.assertEqual(report["theme_title"], "短信 & 通知")

    def test_default_metadata_title_prefers_nondefault_project_name(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "电话.hwt"
            _, report = export_theme(ThemeProject(name="短信工程"), self.catalog, output)
            with ZipFile(output) as archive:
                root = parse_xml(archive.read("description.xml"))
            self.assertEqual(root.findtext("title"), "短信工程")
            self.assertEqual(report["theme_title"], "短信工程")

    def test_custom_metadata_title_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "电话.hwt"
            project = ThemeProject(name="短信工程", title="明确标题 <正式版>")
            _, report = export_theme(project, self.catalog, output)
            with ZipFile(output) as archive:
                root = parse_xml(archive.read("description.xml"))
            self.assertEqual(root.findtext("title"), "明确标题 <正式版>")
            self.assertEqual(root.findtext("title-cn"), "明确标题 <正式版>")
            self.assertEqual(report["theme_title"], "明确标题 <正式版>")

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

    def test_export_rejects_symlinked_output_parent(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            link = root / "exports"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建目录符号链接：{exc}")
            output = link / "theme.hwt"
            with self.assertRaisesRegex(ValueError, "导出目录.*符号链接"):
                export_theme(ThemeProject(), self.catalog, output)
            self.assertFalse((outside / output.name).exists())

    def test_export_rejects_symlinked_archive_temp(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "theme.hwt"
            outside = root / "outside.tmp"
            outside.write_text("keep", encoding="utf-8")
            temp = unique_temp_path(output)
            try:
                os.symlink(outside, temp)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建文件符号链接：{exc}")
            with self.assertRaisesRegex(ValueError, "导出临时文件.*普通文件"):
                export_theme(ThemeProject(), self.catalog, output)
            self.assertTrue(temp.is_symlink())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
            self.assertFalse(output.exists())

    def test_export_report_rejects_symlinked_target_without_removing_export(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "theme.hwt"
            report_path = output.with_suffix(".report.json")
            outside = root / "outside-report.json"
            outside.write_text("keep", encoding="utf-8")
            try:
                os.symlink(outside, report_path)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建文件符号链接：{exc}")
            _, report = export_theme(ThemeProject(), self.catalog, output)
            self.assertTrue(output.is_file())
            self.assertIn("报告写入失败", report["report_warning"])
            self.assertTrue(report_path.is_symlink())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_export_report_rejects_symlinked_temp_without_removing_it(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "theme.hwt"
            report_path = output.with_suffix(".report.json")
            outside = root / "outside-report.tmp"
            outside.write_text("keep", encoding="utf-8")
            report_temp = unique_temp_path(report_path)
            try:
                os.symlink(outside, report_temp)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建文件符号链接：{exc}")
            _, report = export_theme(ThemeProject(), self.catalog, output)
            self.assertTrue(output.is_file())
            self.assertIn("报告写入失败", report["report_warning"])
            self.assertTrue(report_temp.is_symlink())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

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

            for field, invalid, message in (
                ("fit", "unknown", "fit"),
                ("focus_x", float("nan"), "数字"),
                ("enhance_strength", 2, "0 到 1"),
            ):
                payload = {
                    "schema": 2,
                    "changes": {"slot": {"slot_id": "slot", field: invalid}},
                }
                malformed.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    load_project(malformed)

            invalid_project = ThemeProject(changes={"slot": ResourceChange(slot_id="slot", focus_x=float("nan"))})
            with self.assertRaisesRegex(ValueError, "数字"):
                save_project(invalid_project, root / "invalid-save.hwtproj.json")

            custom = ResourceSlot(
                id="__custom__::loader",
                module="com.example.app",
                container="theme.xml",
                resource_type="color",
                name="accent",
                path="theme.xml",
                category="高级自定义",
                label="自定义颜色",
            ).to_dict()
            for field, invalid in (("id", []), ("module", {"name": "bad"}), ("width", "wide")):
                payload = {"schema": 2, "custom_resources": [copy.deepcopy(custom)]}
                payload["custom_resources"][0][field] = invalid
                malformed.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, field):
                    load_project(malformed)

            duplicate = {"schema": 2, "custom_resources": [copy.deepcopy(custom), copy.deepcopy(custom)]}
            malformed.write_text(json.dumps(duplicate, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "ID 重复"):
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

            with patch.object(Path, "write_bytes", fail_project_write), self.assertRaisesRegex(OSError, "不可写"):
                save_project(project, target)
            self.assertFalse(target.exists())
            self.assertFalse(project_assets_dir(target).exists())
            self.assertEqual(list(root.glob(".*.tmp")), [])
            self.assertTrue(project.dirty)
            self.assertIsNone(project.project_file)

    def test_project_save_rejects_asset_changed_during_copy(self):
        slot = next(item for item in self.catalog.resources if item.resource_type == "wallpaper")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wallpaper.png"
            source.write_bytes(b"original")
            target = root / "theme.hwtproj.json"
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=slot.id, source_file=str(source)))
            original_copy2 = shutil.copy2

            def copy_then_mutate(source_path, destination):
                result = original_copy2(source_path, destination)
                Path(source_path).write_bytes(b"changed")
                return result

            with patch("hwtstudio.services.project_assets.shutil.copy2", side_effect=copy_then_mutate), self.assertRaisesRegex(OSError, "复制时发生变化"):
                save_project(project, target)

            self.assertFalse(target.exists())
            self.assertFalse(project_assets_dir(target).exists())
            self.assertTrue(project.dirty)

    def test_project_save_rejects_symlinked_asset_entries(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "theme.hwtproj.json"
            asset_dir = project_assets_dir(target)
            asset_dir.mkdir()
            outside = root / "outside.bin"
            outside.write_bytes(b"outside")
            try:
                os.symlink(outside, asset_dir / "linked.bin")
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建符号链接：{exc}")

            with self.assertRaisesRegex(ValueError, "不能包含符号链接"):
                save_project(ThemeProject(), target)

            self.assertFalse(target.exists())
            self.assertTrue((asset_dir / "linked.bin").is_symlink())

    def test_project_save_preserves_symlinked_transaction_file(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "theme.hwtproj.json"
            outside = root / "outside.json"
            outside.write_text("keep", encoding="utf-8")
            temp = unique_temp_path(target)
            try:
                os.symlink(outside, temp)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建文件符号链接：{exc}")
            with self.assertRaisesRegex(ValueError, "工程事务临时对象.*符号链接"):
                save_project(ThemeProject(), target)
            self.assertTrue(temp.is_symlink())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
            self.assertFalse(target.exists())

    def test_project_save_preserves_unsafe_staging_directory(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "theme.hwtproj.json"
            asset_stage = unique_temp_path(project_assets_dir(target), suffix=".tmp")
            asset_stage.mkdir()
            outside = root / "outside.bin"
            outside.write_bytes(b"keep")
            try:
                os.symlink(outside, asset_stage / "linked.bin")
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建文件符号链接：{exc}")
            with self.assertRaisesRegex(ValueError, "工程资产目录不能包含符号链接"):
                save_project(ThemeProject(), target)
            self.assertTrue(asset_stage.is_dir())
            self.assertTrue((asset_stage / "linked.bin").is_symlink())
            self.assertEqual(outside.read_bytes(), b"keep")
            self.assertFalse(target.exists())

    def test_collect_project_assets_rejects_symlinked_staging_directory(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        slot = next(item for item in self.catalog.resources if item.resource_type == "wallpaper")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wallpaper.png"
            source.write_bytes(b"image")
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=slot.id, source_file=str(source)))
            outside = root / "outside"
            outside.mkdir()
            link = root / "staging"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建目录符号链接：{exc}")
            with self.assertRaisesRegex(ValueError, "工程资产暂存目录.*普通目录"):
                collect_project_assets(
                    project,
                    root / "theme.hwtproj.json",
                    project.to_dict(),
                    staging_dir=link,
                )
            self.assertEqual(list(outside.iterdir()), [])

    def test_collect_project_assets_rejects_symlinked_target_and_copy_temp(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        slot = next(item for item in self.catalog.resources if item.resource_type == "wallpaper")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wallpaper.png"
            source.write_bytes(b"image")
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=slot.id, source_file=str(source)))
            staging = root / "staging"
            staging.mkdir()
            target = staging / asset_name(slot.id, source.name)
            outside = root / "outside.bin"
            outside.write_bytes(b"keep")
            try:
                os.symlink(outside, target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建文件符号链接：{exc}")
            with self.assertRaisesRegex(ValueError, "工程资产目标.*普通文件"):
                collect_project_assets(
                    project,
                    root / "theme.hwtproj.json",
                    project.to_dict(),
                    staging_dir=staging,
                )
            self.assertTrue(target.is_symlink())
            self.assertEqual(outside.read_bytes(), b"keep")

            target.unlink()
            copy_temp = unique_temp_path(target)
            try:
                os.symlink(outside, copy_temp)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建文件符号链接：{exc}")
            with self.assertRaisesRegex(ValueError, "工程资产临时文件.*普通文件"):
                collect_project_assets(
                    project,
                    root / "theme.hwtproj.json",
                    project.to_dict(),
                    staging_dir=staging,
                )
            self.assertTrue(copy_temp.is_symlink())
            self.assertEqual(outside.read_bytes(), b"keep")

    def test_project_save_rejects_non_file_targets_without_replacing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "theme.hwtproj.json"
            target.mkdir()

            with self.assertRaisesRegex(ValueError, "工程文件目标"):
                save_project(ThemeProject(), target)

            self.assertTrue(target.is_dir())

            target.rmdir()
            asset_dir = project_assets_dir(target)
            asset_dir.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "工程资产目录"):
                save_project(ThemeProject(), target)

            self.assertTrue(asset_dir.is_file())
            self.assertEqual(asset_dir.read_text(encoding="utf-8"), "keep")

    def test_project_save_rejects_symlinked_output_parent(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            link = root / "projects"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建目录符号链接：{exc}")
            target = link / "theme.hwtproj.json"
            with self.assertRaisesRegex(ValueError, "工程保存目录.*符号链接"):
                save_project(ThemeProject(), target)
            self.assertFalse((outside / target.name).exists())

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

            with patch("hwtstudio.projectio.os.replace", side_effect=fail_new_project), self.assertRaisesRegex(OSError, "提交失败"):
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
            with ZipFile(output) as archive, Image.open(BytesIO(archive.read(slot.path))) as image:
                self.assertEqual(image.size, (slot.width, slot.height))

    def test_export_rejects_source_mutation_after_preflight(self):
        slot = next(item for item in self.catalog.resources if item.resource_type == "wallpaper")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "wallpaper.png"
            Image.new("RGBA", (slot.width, slot.height), (20, 40, 60, 255)).save(source)
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=slot.id, source_file=str(source)))
            output = Path(directory) / "mutated.hwt"

            def mutate_then_render(path, target_slot, change):
                Image.new("RGBA", (slot.width, slot.height), (60, 40, 20, 255)).save(path)
                return render_source_image(path, target_slot, change)

            with patch("hwtstudio.exporter.render_image", side_effect=mutate_then_render), self.assertRaisesRegex(ValueError, "源文件在导出期间发生变化"):
                export_theme(project, self.catalog, output)
            self.assertFalse(output.exists())

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
        self.assertEqual(honor_resource_name("navigationbar_emui_light"), "navigationbar_magic_light")
        self.assertEqual(honor_resource_name("dial_hwfab_shadow_start"), "dial_hnfab_shadow_start")
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

    def test_huawei_mapping_does_not_rewrite_unrelated_substrings(self):
        self.assertEqual(honor_resource_name("myhwvalue"), "myhwvalue")
        self.assertEqual(honor_resource_name("emui2_panel"), "emui2_panel")
        self.assertEqual(
            honor_resource_path("dynamic_icons/com.huawei2/weather.png"),
            "dynamic_icons/com.huawei2/weather.png",
        )
        self.assertEqual(
            honor_resource_path("res/drawable/myemui_panel.png"),
            "res/drawable/myemui_panel.png",
        )

    def test_huawei_icon_fanout_is_exact_and_module_scoped(self):
        self.assertEqual(
            honor_resource_paths("icons", "com.vmall.client.png"),
            ("com.hihonor.hstore.global.png", "com.hihonor.appmarket.png"),
        )
        self.assertEqual(
            honor_resource_paths("com.example", "com.vmall.client.png"),
            ("com.vmall.client.png",),
        )
        self.assertEqual(
            honor_resource_paths("icons", "com.vmall.clientp.png"),
            ("com.vmall.clientp.png",),
        )

    def test_huawei_icon_fanout_exports_both_targets_and_reports_mapping(self):
        source = next(item for item in self.catalog.resources if item.path == "com.vmall.client.png")
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "icon.png"
            Image.new("RGBA", (64, 64), (20, 40, 60, 255)).save(image)
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=source.id, source_file=str(image)))
            output = Path(directory) / "fanout.hwt"
            _, report = export_theme(project, self.catalog, output)

            fanout = [item for item in report["preflight"]["warnings"] if item["kind"] == "resource_fanout"]
            self.assertEqual(len(fanout), 1)
            self.assertEqual(
                fanout[0]["targets"],
                [
                    {"module": "icons", "path": "com.hihonor.hstore.global.png"},
                    {"module": "icons", "path": "com.hihonor.appmarket.png"},
                ],
            )
            with ZipFile(output) as outer, ZipFile(BytesIO(outer.read("icons"))) as icons:
                self.assertEqual(
                    set(icons.namelist()),
                    {"com.hihonor.hstore.global.png", "com.hihonor.appmarket.png"},
                )
            self.assertEqual(
                {item["path"] for item in report["applied"]},
                {"com.hihonor.hstore.global.png", "com.hihonor.appmarket.png"},
            )

    def test_huawei_icon_fanout_keeps_native_target_priority_per_path(self):
        source = ResourceSlot(
            id="__source__::vmall",
            module="icons",
            container="",
            resource_type="icon",
            name="com.vmall.client.png",
            path="com.vmall.client.png",
            category="桌面图标",
            label="华为商城图标",
            width=8,
            height=8,
        )
        native = ResourceSlot(
            id="__native__::appmarket",
            module="icons",
            container="",
            resource_type="icon",
            name="com.hihonor.appmarket.png",
            path="com.hihonor.appmarket.png",
            category="桌面图标",
            label="荣耀应用市场图标",
            width=8,
            height=8,
        )
        catalog = ThemeCatalog("", "", "", {}, [], [source, native])
        with tempfile.TemporaryDirectory() as directory:
            source_image = Path(directory) / "source.png"
            native_image = Path(directory) / "native.png"
            Image.new("RGBA", (8, 8), (220, 20, 20, 255)).save(source_image)
            Image.new("RGBA", (8, 8), (20, 20, 220, 255)).save(native_image)
            project = ThemeProject()
            project.set_change(ResourceChange(slot_id=source.id, source_file=str(source_image)))
            project.set_change(ResourceChange(slot_id=native.id, source_file=str(native_image)))
            result = preflight_export(project, catalog)
            resolved = [item for item in result["warnings"] if item["kind"] == "duplicate_target_resolved"]
            self.assertTrue(result["valid"])
            self.assertEqual(result["image_targets"], 2)
            self.assertEqual(len(resolved), 1)
            self.assertEqual(resolved[0]["selected_slot_id"], native.id)

            output = Path(directory) / "native-priority.hwt"
            export_theme(project, catalog, output)
            # ZipFile 的表达式必须在外层归档打开后再求值，这里必须保持嵌套写法。
            with ZipFile(output) as outer:
                icons_bytes = outer.read("icons")
            with ZipFile(BytesIO(icons_bytes)) as icons:
                with Image.open(BytesIO(icons.read("com.hihonor.hstore.global.png"))) as image:
                    self.assertEqual(image.getpixel((0, 0))[:3], (220, 20, 20))
                with Image.open(BytesIO(icons.read("com.hihonor.appmarket.png"))) as image:
                    self.assertEqual(image.getpixel((0, 0))[:3], (20, 20, 220))

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
            with ZipFile(output) as outer, ZipFile(BytesIO(outer.read("framework-res-hnext"))) as module:
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

    def test_archive_paths_reject_empty_and_dot_segments(self):
        self.assertTrue(is_safe_archive_path("preview/"))
        self.assertTrue(is_safe_archive_path("preview/cover.jpg"))
        self.assertFalse(is_safe_archive_path("preview//cover.jpg"))
        self.assertFalse(is_safe_archive_path("preview/./cover.jpg"))
        self.assertFalse(is_safe_archive_path("preview/../cover.jpg"))

    def test_validator_rejects_physically_overlapping_zip_data(self):
        def overlap_archive() -> bytes:
            raw = BytesIO()
            with ZipFile(raw, "w", ZIP_DEFLATED) as archive:
                archive.writestr("first.bin", b"A" * 64)
                archive.writestr("second.bin", b"A" * 64)
            payload = bytearray(raw.getvalue())
            first = payload.find(b"PK\x01\x02")
            second = payload.find(b"PK\x01\x02", first + 4)
            self.assertGreaterEqual(first, 0)
            self.assertGreater(second, first)
            first_offset = struct.unpack_from("<I", payload, first + 42)[0]
            struct.pack_into("<I", payload, second + 42, first_offset)
            return bytes(payload)

        payload = overlap_archive()
        with ZipFile(BytesIO(payload)) as archive:
            self.assertEqual(
                archive_data_overlaps(archive.infolist(), archive.fp),
                [("first.bin", "second.bin")],
            )

        with tempfile.TemporaryDirectory() as directory:
            outer_path = Path(directory) / "overlap.hwt"
            outer_path.write_bytes(payload)
            result = validate_theme(outer_path)
            self.assertIn("data_overlap", {item["kind"] for item in result["errors"]})

            nested_path = Path(directory) / "nested-overlap.hwt"
            with ZipFile(nested_path, "w", ZIP_DEFLATED) as outer:
                outer.writestr("com.example", payload)
            nested_result = validate_theme(nested_path)
            self.assertIn("nested_data_overlap", {item["kind"] for item in nested_result["errors"]})
            catalog = scan_theme(nested_path)
            self.assertIn("nested_data_overlap", {item["kind"] for item in catalog.warnings})

    def test_validator_rejects_central_directory_local_header_mismatch(self):
        raw = BytesIO()
        payload = b"same payload"
        with ZipFile(raw, "w") as archive:
            archive.writestr("description.xml", payload)
            archive.writestr("second.bin", payload)
        encoded = bytearray(raw.getvalue())
        first = encoded.find(b"PK\x01\x02")
        second = encoded.find(b"PK\x01\x02", first + 4)
        self.assertGreaterEqual(first, 0)
        self.assertGreater(second, first)
        first_offset = struct.unpack_from("<I", encoded, first + 42)[0]
        second_offset = struct.unpack_from("<I", encoded, second + 42)[0]
        struct.pack_into("<I", encoded, first + 42, second_offset)
        struct.pack_into("<I", encoded, second + 42, first_offset)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local-header-mismatch.hwt"
            path.write_bytes(encoded)
            result = validate_theme(path)
            self.assertIn("local_header_mismatch", {item["kind"] for item in result["errors"]})
            catalog = scan_theme(path)
            self.assertIn("local_header_mismatch", {item["kind"] for item in catalog.warnings})

    def test_validator_rejects_local_header_metadata_mismatch(self):
        raw = BytesIO()
        with ZipFile(raw, "w", ZIP_STORED) as archive:
            archive.writestr("description.xml", b"<HwTheme/>")
        encoded = bytearray(raw.getvalue())
        local_crc = struct.unpack_from("<I", encoded, 14)[0]
        struct.pack_into("<I", encoded, 14, local_crc ^ 1)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local-header-metadata-mismatch.hwt"
            path.write_bytes(encoded)
            result = validate_theme(path)
            self.assertIn("local_header_mismatch", {item["kind"] for item in result["errors"]})
            self.assertIn(
                "local_header_metadata_mismatch",
                {item["message"] for item in result["errors"] if item["kind"] == "local_header_mismatch"},
            )
            catalog = scan_theme(path)
            self.assertIn("local_header_mismatch", {item["kind"] for item in catalog.warnings})

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

    def test_preferred_catalog_rejects_cache_when_source_hash_changes(self):
        slot = copy.deepcopy(self.catalog.resources[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.hwt"
            source.write_bytes(b"original source")
            cached_catalog = ThemeCatalog(
                str(source),
                catalog_service._bounded_sha256(source) or "",
                "now",
                {"resource_slots": 1},
                [],
                [slot],
            )
            save_catalog(cached_catalog, root / "catalog_daxue.json")
            source.write_bytes(b"replaced source")

            with patch("hwtstudio.services.catalog_service.data_dir", return_value=root):
                loaded, warning = load_preferred_catalog()

            self.assertNotEqual(loaded.source_path, str(source))
            self.assertIn("已过期", warning)

    def test_user_catalog_save_writes_source_compatibility_report(self):
        catalog = ThemeCatalog(
            "source.hwt",
            "a" * 64,
            "now",
            {"modules": 1, "resource_slots": 0},
            [{"kind": "nonstandard_xml", "path": "com.example/theme.xml"}],
            [],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("hwtstudio.services.catalog_service.data_dir", return_value=root):
                save_user_catalog(catalog)
            report = json.loads((root / "source_compatibility.report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["compatibility_warnings"], 1)
            self.assertTrue((root / "catalog_daxue.json").is_file())

    def test_user_catalog_save_rejects_symlinked_targets(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        catalog = ThemeCatalog("source.hwt", "a" * 64, "now", {"modules": 1}, [], [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.json"
            outside.write_text("keep", encoding="utf-8")
            target = root / catalog_service._CATALOG_FILE_NAME
            try:
                os.symlink(outside, target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建符号链接：{exc}")

            with patch("hwtstudio.services.catalog_service.data_dir", return_value=root), self.assertRaisesRegex(OSError, "符号链接"):
                save_user_catalog(catalog)

            self.assertTrue(target.is_symlink())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_catalog_save_rejects_symlinked_output_parent(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        catalog = ThemeCatalog("source.hwt", "a" * 64, "now", {"modules": 1}, [], [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            link = root / "exports"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建目录符号链接：{exc}")
            with self.assertRaisesRegex(ValueError, "资源目录输出目录.*符号链接"):
                save_catalog(catalog, link / "catalog.json")
            self.assertEqual(list(outside.iterdir()), [])

    def test_catalog_save_rejects_symlinked_output_target(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        catalog = ThemeCatalog("source.hwt", "a" * 64, "now", {"modules": 1}, [], [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.json"
            outside.write_text("keep", encoding="utf-8")
            target = root / "catalog.json"
            try:
                os.symlink(outside, target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建文件符号链接：{exc}")
            with self.assertRaisesRegex(ValueError, "资源目录输出目标.*普通文件"):
                save_catalog(catalog, target)
            self.assertTrue(target.is_symlink())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_user_catalog_recovery_cleans_dangling_transaction_symlink(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / catalog_service._TRANSACTION_FILE_NAME
            outside = root / "missing-marker-target.json"
            try:
                os.symlink(outside, marker)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建符号链接：{exc}")
            recovered, warning = catalog_service._recover_catalog_transaction(root)

            self.assertTrue(recovered)
            self.assertIn("不是普通文件", warning)
            self.assertFalse(marker.is_symlink())

    def test_user_catalog_recovery_fails_closed_for_non_file_transaction_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / catalog_service._TRANSACTION_FILE_NAME
            marker.mkdir()

            recovered, warning = catalog_service._recover_catalog_transaction(root)

            self.assertFalse(recovered)
            self.assertIn("不是普通文件", warning)
            self.assertTrue(marker.is_dir())

    def test_user_catalog_transaction_rejects_symlinked_marker_and_temp(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.json"
            outside.write_text("keep", encoding="utf-8")
            marker = root / catalog_service._TRANSACTION_FILE_NAME
            try:
                os.symlink(outside, marker)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建文件符号链接：{exc}")
            with self.assertRaisesRegex(OSError, "事务记录.*普通文件"):
                catalog_service._write_transaction(root, [])
            self.assertTrue(marker.is_symlink())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

            marker.unlink()
            temp = unique_temp_path(marker, suffix=".tmp")
            try:
                os.symlink(outside, temp)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建文件符号链接：{exc}")
            with self.assertRaisesRegex(OSError, "事务临时文件.*普通文件"):
                catalog_service._write_transaction(root, [])
            self.assertTrue(temp.is_symlink())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_user_catalog_load_falls_back_when_bundle_lock_is_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("hwtstudio.services.catalog_service.data_dir", return_value=root),
                patch(
                    "hwtstudio.services.catalog_service.interprocess_lock",
                    side_effect=InterprocessLockTimeoutError("busy"),
                ),
            ):
                loaded, warning = load_preferred_catalog()

            self.assertTrue(loaded.resources)
            self.assertIn("内置目录", warning)

    def test_user_catalog_save_fails_before_writing_when_bundle_lock_is_busy(self):
        catalog = ThemeCatalog("source.hwt", "a" * 64, "now", {"modules": 1}, [], [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("hwtstudio.services.catalog_service.data_dir", return_value=root),
                patch(
                    "hwtstudio.services.catalog_service.interprocess_lock",
                    side_effect=InterprocessLockTimeoutError("busy"),
                ),self.assertRaisesRegex(OSError, "busy")
            ):
                save_user_catalog(catalog)

            self.assertFalse((root / catalog_service._CATALOG_FILE_NAME).exists())
            self.assertFalse((root / catalog_service._REPORT_FILE_NAME).exists())

    def test_user_catalog_save_serializes_threads_before_bundle_commit(self):
        first = ThemeCatalog("first.hwt", "a" * 64, "first", {"modules": 1}, [], [])
        second = ThemeCatalog("second.hwt", "b" * 64, "second", {"modules": 2}, [], [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = 0
            maximum_active = 0
            state_lock = threading.Lock()
            entered = threading.Event()
            release = threading.Event()

            def hold_bundle(_catalog, _root):
                nonlocal active, maximum_active
                with state_lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                entered.set()
                if not release.wait(2):
                    raise AssertionError("测试未释放目录事务")
                with state_lock:
                    active -= 1

            with (
                patch("hwtstudio.services.catalog_service.data_dir", return_value=root),
                patch("hwtstudio.services.catalog_service._save_catalog_bundle", side_effect=hold_bundle),
                ThreadPoolExecutor(max_workers=2) as executor,
            ):
                first_future = executor.submit(save_user_catalog, first)
                self.assertTrue(entered.wait(2))
                second_future = executor.submit(save_user_catalog, second)
                time.sleep(0.1)
                with state_lock:
                    self.assertEqual(active, 1)
                release.set()
                first_future.result(timeout=5)
                second_future.result(timeout=5)

            self.assertEqual(maximum_active, 1)

    def test_user_catalog_save_rolls_back_both_files_when_second_replace_fails(self):
        old = ThemeCatalog("old.hwt", "a" * 64, "old", {"modules": 1}, [], [])
        new = ThemeCatalog("new.hwt", "b" * 64, "new", {"modules": 2}, [], [])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("hwtstudio.services.catalog_service.data_dir", return_value=root):
                save_user_catalog(old)
            old_catalog = (root / "catalog_daxue.json").read_bytes()
            old_report = (root / "source_compatibility.report.json").read_bytes()
            real_replace = os.replace
            failed = False

            def fail_report_replace(source, target):
                nonlocal failed
                if Path(target).name == "source_compatibility.report.json" and not failed:
                    failed = True
                    raise OSError("模拟报告提交失败")
                return real_replace(source, target)

            with patch("hwtstudio.services.catalog_service.os.replace", side_effect=fail_report_replace), patch("hwtstudio.services.catalog_service.data_dir", return_value=root), self.assertRaisesRegex(OSError, "提交校验失败"):
                save_user_catalog(new)

            self.assertEqual((root / "catalog_daxue.json").read_bytes(), old_catalog)
            self.assertEqual((root / "source_compatibility.report.json").read_bytes(), old_report)
            self.assertFalse((root / ".catalog_bundle.transaction.json").exists())
            self.assertEqual(list(root.glob("*.pending")), [])
            self.assertEqual(list(root.glob("*.backup")), [])

    def test_user_catalog_load_recovers_after_first_file_was_replaced(self):
        slot = copy.deepcopy(self.catalog.resources[0])
        old = ThemeCatalog("old.hwt", "a" * 64, "old", {"modules": 1}, [], [slot])
        new = ThemeCatalog("new.hwt", "b" * 64, "new", {"modules": 2}, [], [slot])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("hwtstudio.services.catalog_service.data_dir", return_value=root):
                save_user_catalog(old)
            targets = [root / catalog_service._CATALOG_FILE_NAME, root / catalog_service._REPORT_FILE_NAME]
            stages = [catalog_service.unique_temp_path(target, suffix=".pending") for target in targets]
            backups = [catalog_service.unique_temp_path(target, suffix=".backup") for target in targets]
            save_catalog(new, stages[0])
            save_source_compatibility_report(new, stages[1])
            entries = []
            for target, stage, backup in zip(targets, stages, backups, strict=True):
                shutil.copyfile(target, backup)
                entries.append({
                    "target": target.name,
                    "stage": stage.name,
                    "backup": backup.name,
                    "sha256": catalog_service._bounded_sha256(stage),
                    "backup_sha256": catalog_service._bounded_sha256(backup),
                    "original_exists": True,
                })
            catalog_service._write_transaction(root, entries)
            os.replace(stages[0], targets[0])

            with patch("hwtstudio.services.catalog_service.data_dir", return_value=root):
                loaded, warning = load_preferred_catalog()

            self.assertEqual(loaded.source_path, "new.hwt")
            self.assertIn("恢复", warning)
            report = json.loads(targets[1].read_text(encoding="utf-8"))
            self.assertEqual(report["source_path"], "new.hwt")
            self.assertFalse((root / ".catalog_bundle.transaction.json").exists())

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

            with patch("hwtstudio.ssh_transfer.local_sha256", side_effect=hash_then_mutate), self.assertRaisesRegex(RuntimeError, "校验后"):
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
            with (
                patch(
                    "hwtstudio.ssh_transfer.preflight_phone",
                    return_value={"valid": True, "checks": [], "errors": [], "warnings": []},
                ),
                patch("hwtstudio.ssh_transfer._run_with_cancel", side_effect=run_command),
                self.assertRaises(TransferCancelled),
            ):
                transfer_to_phone(path, cancelled=cancelled)

            self.assertEqual(len(calls), 3)
            self.assertIn("rm -f", calls[-1][2])

    @patch("hwtstudio.ssh_transfer.uuid.uuid4")
    def test_ssh_transfer_uses_unique_remote_temp_name(self, uuid4):
        uuid4.return_value.hex = "a" * 32

        def result(returncode=0, stdout="", stderr=""):
            return type("Result", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr})()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.hwt"
            path.write_bytes(b"payload")
            digest = hashlib.sha256(b"payload").hexdigest()
            with patch(
                "hwtstudio.ssh_transfer.preflight_phone",
                return_value={"valid": True, "checks": [], "errors": [], "warnings": []},
            ), patch("hwtstudio.ssh_transfer._run_with_cancel") as run:
                run.side_effect = [
                    result(),
                    result(),
                    result(stdout=f"{digest}  theme.hwt\n"),
                    result(),
                    result(),
                    result(),
                ]
                transfer_to_phone(path, host="phone-termux")

            commands = [call.args[0] for call in run.call_args_list]
            temp_suffix = f".{uuid4.return_value.hex}.uploading"
            self.assertIn(f"{REMOTE_DIR}/theme.hwt{temp_suffix}", commands[0][2])
            self.assertIn(f"phone-termux:{REMOTE_DIR}/theme.hwt{temp_suffix}", commands[1][2])
            self.assertIn(f"{REMOTE_DIR}/theme.hwt{temp_suffix}", commands[2][2])
            self.assertIn(f"{REMOTE_DIR}/theme.hwt{temp_suffix}", commands[3][2])
            self.assertNotIn(f"{REMOTE_DIR}/theme.hwt.uploading", commands[0][2])
            self.assertIn("termux-media-scan", commands[4][2])
            self.assertIn(f"{REMOTE_DIR}/theme.hwt", commands[4][2])
            self.assertIn("am start", commands[5][2])


if __name__ == "__main__":
    unittest.main()
