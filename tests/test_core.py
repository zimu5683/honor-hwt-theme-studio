from __future__ import annotations

import hashlib
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from PIL import Image

from hwtstudio.blank import DIRECTORY_ENTRIES, IMAGE_LAYOUT, create_blank_theme
from hwtstudio.catalog import load_catalog, scan_theme
from hwtstudio.exporter import export_theme
from hwtstudio.models import ResourceChange, ResourceSlot, ThemeProject
from hwtstudio.projectio import load_project, save_project
from hwtstudio.paths import bundled_catalog, default_source_theme
from hwtstudio.validation import validate_theme


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog(bundled_catalog())

    def test_source_hash_and_inventory(self):
        self.assertEqual(
            self.catalog.source_sha256.upper(),
            "8B74402CF233FAA5693BA63D9679653FD562A9671AAE0CAFC55E5FD0278F17D8",
        )
        self.assertGreaterEqual(self.catalog.stats["color_slots"], 10_000)
        self.assertEqual(self.catalog.stats["icon_slots"], 1688)
        self.assertEqual(self.catalog.stats["wallpaper_slots"], 2)
        self.assertEqual(self.catalog.stats["preview_slots"], 7)

    def test_blank_theme_minimal_and_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "blank.hwt"
            create_blank_theme(output)
            result = validate_theme(output)
            self.assertTrue(result["valid"], result)
            with ZipFile(output) as archive:
                self.assertEqual(archive.testzip(), None)
                self.assertEqual(
                    set(archive.namelist()),
                    {
                        "description.xml",
                        "unlock/theme.xml",
                        "icons",
                        *DIRECTORY_ENTRIES,
                        *IMAGE_LAYOUT.keys(),
                    },
                )
                self.assertFalse(any(name.startswith("com.") for name in archive.namelist()))
                with ZipFile(BytesIO(archive.read("icons"))) as icons:
                    self.assertEqual(icons.namelist(), [])

    def test_honor_local_theme_admission_entries_are_required(self):
        """Mirror Theme Manager 20.x isValidThemeInfo()'s local-HWT gate."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "blank.hwt"
            create_blank_theme(output)
            with ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn("preview/", names)
                self.assertIn("icons", names)

    def test_color_only_export_adds_only_one_module(self):
        slot = next(x for x in self.catalog.resources if x.resource_type == "color" and x.module == "com.android.settings")
        project = ThemeProject(name="颜色测试")
        project.set_change(ResourceChange(slot_id=slot.id, value="#FF336699"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "color.hwt"
            _, report = export_theme(project, self.catalog, output)
            self.assertTrue(report["validation"]["valid"])
            with ZipFile(output) as outer:
                modules = [n for n in outer.namelist() if n.startswith("com.")]
                self.assertEqual(modules, ["com.android.settings"])
                with ZipFile(BytesIO(outer.read("com.android.settings"))) as module:
                    xml = module.read(slot.container).decode("utf-8")
                    self.assertIn(slot.name, xml)
                    self.assertIn("#FF336699", xml)

    def test_disable_resource_removes_module(self):
        slot = next(x for x in self.catalog.resources if x.resource_type == "color" and x.module == "com.android.systemui")
        project = ThemeProject(name="禁用测试")
        project.set_change(ResourceChange(slot_id=slot.id, enabled=False, value="#FFFFFFFF"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "disabled.hwt"
            export_theme(project, self.catalog, output)
            with ZipFile(output) as outer:
                self.assertNotIn("com.android.systemui", outer.namelist())

    def test_synthetic_settings_background(self):
        slot = next(x for x in self.catalog.resources if x.id == "__synthetic__::background::设置背景")
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "source.jpg"
            Image.new("RGB", (300, 600), (30, 60, 90)).save(image_path)
            project = ThemeProject(name="背景测试")
            project.set_change(ResourceChange(slot_id=slot.id, source_file=str(image_path)))
            output = Path(directory) / "background.hwt"
            export_theme(project, self.catalog, output)
            with ZipFile(output) as outer:
                with ZipFile(BytesIO(outer.read("com.android.settings"))) as module:
                    self.assertIn(
                        "framework-res-hnext/res/drawable-xxhdpi/background_magic.9.png",
                        module.namelist(),
                    )
                    rendered = module.read("framework-res-hnext/res/drawable-xxhdpi/background_magic.9.png")
                    with Image.open(BytesIO(rendered)) as image:
                        self.assertEqual(image.size, (1220, 2700))

    def test_original_file_is_not_modified_by_scan(self):
        source = default_source_theme()
        if not source.exists():
            self.skipTest("源主题不在默认路径")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        scan_theme(source)
        after = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_custom_resource_round_trip_and_export(self):
        slot = ResourceSlot(
            id="__custom__::test",
            module="com.example.app",
            container="theme.xml",
            resource_type="color",
            name="example_color",
            path="theme.xml",
            category="高级自定义",
            label="自定义颜色",
            status="可能支持",
            risk="高",
        )
        project = ThemeProject(name="自定义测试", custom_resources=[slot])
        project.set_change(ResourceChange(slot_id=slot.id, value="#FF102030"))
        with tempfile.TemporaryDirectory() as directory:
            project_file = Path(directory) / "test.hwtproj.json"
            save_project(project, project_file)
            loaded = load_project(project_file)
            self.assertEqual(loaded.custom_resources[0].name, "example_color")
            output = Path(directory) / "custom.hwt"
            export_theme(loaded, self.catalog, output)
            with ZipFile(output) as outer:
                self.assertIn("com.example.app", outer.namelist())


if __name__ == "__main__":
    unittest.main()
