from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
import warnings
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image

from hwtstudio.blank import create_blank_theme
from hwtstudio.catalog import load_catalog
from hwtstudio.catalog import save_catalog
from hwtstudio.exporter import export_theme, preflight_export
from hwtstudio.models import ResourceChange, ResourceSlot, ThemeProject
from hwtstudio.models import ThemeCatalog
from hwtstudio.paths import bundled_catalog
from hwtstudio.pngmeta import extract_android_chunks, inject_android_chunks
from hwtstudio.projectio import load_project, project_assets_dir, save_project
from hwtstudio.ssh_transfer import preflight_phone
from hwtstudio.services.catalog_service import load_preferred_catalog
from hwtstudio.ui.dialogs import find_named_files
from hwtstudio.validation import validate_custom_slot, validate_theme


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

    def test_report_write_failure_does_not_remove_export(self):
        slot = next(item for item in self.catalog.resources if item.resource_type == "color")
        project = ThemeProject()
        project.set_change(ResourceChange(slot_id=slot.id, value="#FF112233"))
        original_write_text = Path.write_text

        def guarded_write_text(path, *args, **kwargs):
            if path.name.endswith(".report.json.tmp"):
                raise OSError("read only report directory")
            return original_write_text(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch.object(Path, "write_text", guarded_write_text):
            output = Path(directory) / "output.hwt"
            _, report = export_theme(project, self.catalog, output)
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


if __name__ == "__main__":
    unittest.main()
