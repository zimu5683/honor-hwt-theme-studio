from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image

from hwtstudio.blank import DIRECTORY_ENTRIES, IMAGE_LAYOUT, create_blank_theme
from hwtstudio.catalog import (
    load_catalog,
    save_source_compatibility_report,
    scan_theme,
    source_compatibility_report,
)
from hwtstudio.exporter import ExportCancelled, export_theme
from hwtstudio.imageops import MAX_IMAGE_DIMENSION, load_image, render_image
from hwtstudio.models import ResourceChange, ResourceSlot, ThemeCatalog, ThemeProject
from hwtstudio.paths import bundled_catalog, default_source_theme
from hwtstudio.projectio import load_project, save_project
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

    def test_source_compatibility_report_separates_scan_warnings_from_export_validation(self):
        report = source_compatibility_report(self.catalog)
        summary = report["summary"]
        compatibility_count = sum(
            1 for item in self.catalog.warnings
            if item.get("kind") in report["compatibility"]["warning_kinds"]
        )

        self.assertEqual(summary["compatibility_warnings"], compatibility_count)
        self.assertEqual(
            summary["total_warnings"],
            summary["compatibility_warnings"] + summary["scan_integrity_warnings"],
        )
        self.assertFalse(report["strict_export_validation"]["performed"])

        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "source_compatibility.report.json"
            save_source_compatibility_report(self.catalog, report_path)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["summary"], summary)
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_source_compatibility_report_audits_bounded_honor_mapping_targets(self):
        source_color = ResourceSlot(
            id="source-color",
            module="com.huawei.android.launcher",
            container="framework-res-hwext/theme.xml",
            resource_type="color",
            name="emui_color_bg",
            path="framework-res-hwext/theme.xml",
            category="桌面",
            label="华为背景",
        )
        source_icon = ResourceSlot(
            id="source-icon",
            module="icons",
            container="",
            resource_type="icon",
            name="com.vmall.client.png",
            path="com.vmall.client.png",
            category="桌面图标",
            label="华为商城",
        )
        synthetic = ResourceSlot(
            id="synthetic",
            module="com.android.settings",
            container="",
            resource_type="image",
            name="设置背景",
            path="",
            category="设置",
            label="设置背景",
            synthetic=True,
            targets=[{"module": "com.android.settings", "path": "background.png"}],
        )
        catalog = ThemeCatalog("source.hwt", "a" * 64, "now", {}, [], [source_color, source_icon, synthetic])

        conversion = source_compatibility_report(catalog)["honor_conversion"]

        self.assertEqual(conversion["summary"]["scanned_slots"], 2)
        self.assertEqual(conversion["summary"]["mapped_slots"], 2)
        self.assertEqual(conversion["summary"]["fanout_slots"], 1)
        self.assertEqual(conversion["summary"]["mapped_targets"], 3)
        self.assertFalse(conversion["summary"]["items_truncated"])
        by_id = {item["slot_id"]: item for item in conversion["items"]}
        self.assertEqual(
            by_id["source-color"]["targets"],
            [{
                "module": "com.hihonor.android.launcher",
                "path": "framework-res-hnext/theme.xml",
                "resource_type": "color",
                "name": "magic_color_bg",
            }],
        )
        self.assertEqual(
            by_id["source-icon"]["targets"],
            [
                {"module": "icons", "path": "com.hihonor.hstore.global.png"},
                {"module": "icons", "path": "com.hihonor.appmarket.png"},
            ],
        )

    def test_source_compatibility_report_caps_mapping_samples(self):
        resources = [
            ResourceSlot(
                id=f"source-{index:03d}",
                module="com.huawei.android.launcher",
                container="theme.xml",
                resource_type="color",
                name=f"emui_color_{index:03d}",
                path="theme.xml",
                category="桌面",
                label="华为颜色",
            )
            for index in range(257)
        ]
        conversion = source_compatibility_report(
            ThemeCatalog("source.hwt", "a" * 64, "now", {}, [], resources),
        )["honor_conversion"]
        summary = conversion["summary"]

        self.assertEqual(summary["mapped_slots"], 257)
        self.assertEqual(summary["sampled_items"], summary["sample_limit"])
        self.assertTrue(summary["items_truncated"])
        self.assertEqual(len(conversion["items"]), summary["sample_limit"])

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

    def test_blank_theme_write_failure_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "blank.hwt"
            output.write_bytes(b"previous theme")
            with patch("hwtstudio.blank.ZipFile") as zip_file:
                archive = zip_file.return_value.__enter__.return_value
                archive.writestr.side_effect = OSError("模拟写入失败")
                with self.assertRaisesRegex(OSError, "模拟写入失败"):
                    create_blank_theme(output)
            self.assertEqual(output.read_bytes(), b"previous theme")
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_blank_theme_rejects_symlinked_output_parent(self):
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
            with self.assertRaisesRegex(ValueError, "空白主题输出目录.*符号链接"):
                create_blank_theme(link / "blank.hwt")
            self.assertEqual(list(outside.iterdir()), [])

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

    def test_export_honors_cancellation(self):
        slot = next(x for x in self.catalog.resources if x.resource_type == "color" and x.module == "com.android.settings")
        project = ThemeProject(name="取消测试")
        project.set_change(ResourceChange(slot_id=slot.id, value="#FF336699"))
        cancelled = threading.Event()
        cancelled.set()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cancel.hwt"
            with self.assertRaises(ExportCancelled):
                export_theme(project, self.catalog, output, cancelled=cancelled)
            self.assertFalse(output.exists())

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
            _, report = export_theme(project, self.catalog, output)
            self.assertFalse(any(item["kind"] == "resource_fanout" for item in report["preflight"]["warnings"]))
            with ZipFile(output) as outer, ZipFile(BytesIO(outer.read("com.android.settings"))) as module:
                self.assertIn(
                    "framework-res-hnext/res/drawable-xxhdpi/background_magic.9.png",
                    module.namelist(),
                )
                rendered = module.read("framework-res-hnext/res/drawable-xxhdpi/background_magic.9.png")
                with Image.open(BytesIO(rendered)) as image:
                    self.assertEqual(image.size, (1220, 2700))

    def test_original_file_is_not_modified_by_scan(self):
        source = default_source_theme()
        if not str(source) or not source.is_file():
            self.skipTest("未设置 HWTSTUDIO_SOURCE_THEME 或源主题不存在")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        scan_theme(source)
        after = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_scan_skips_unsafe_outer_and_nested_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            module = BytesIO()
            with ZipFile(module, "w") as nested:
                nested.writestr("../escape.xml", b"<resources><color name='escape'>#FFFFFFFF</color></resources>")
                nested.writestr("theme.xml", b"<resources><color name='safe'>#FF112233</color></resources>")
            source = Path(directory) / "unsafe.hwt"
            with ZipFile(source, "w") as outer:
                outer.writestr("description.xml", b"<HwTheme/>")
                outer.writestr("../outside", module.getvalue())
                outer.writestr("com.example", module.getvalue())

            catalog = scan_theme(source)
            self.assertTrue(any(item.name == "safe" for item in catalog.resources))
            self.assertFalse(any(item.name == "escape" for item in catalog.resources))
            warning_kinds = {item["kind"] for item in catalog.warnings}
            self.assertIn("unsafe_path", warning_kinds)
            self.assertIn("unsafe_nested_path", warning_kinds)
            report = source_compatibility_report(catalog)
            self.assertTrue(any(
                item["kind"] == "unsafe_path"
                for item in report["scan_integrity"]["items"]
            ))

    def test_scan_blocks_dangerous_nested_entries_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            module = BytesIO()
            with ZipFile(module, "w", ZIP_DEFLATED) as nested:
                nested.writestr("theme.xml", b"<resources><color name='safe'>#FF112233</color></resources>")
                nested.writestr("bomb.bin", b"\0" * (2 * 1024 * 1024))
                link = ZipInfo("link")
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                nested.writestr(link, b"../escape.txt")
            source = Path(directory) / "dangerous.hwt"
            with ZipFile(source, "w", ZIP_DEFLATED) as outer:
                outer.writestr("description.xml", b"<HwTheme/>")
                outer.writestr("com.example", module.getvalue())

            catalog = scan_theme(source)
            self.assertTrue(any(item.name == "safe" for item in catalog.resources))
            warning_kinds = {item["kind"] for item in catalog.warnings}
            self.assertIn("nested_compression_ratio", warning_kinds)
            self.assertIn("nested_symlink_entry", warning_kinds)

    def test_scan_reports_outer_crc_check_failure_without_raising(self):
        info = ZipInfo("com.example")
        outer = MagicMock()
        outer.__enter__.return_value = outer
        outer.__exit__.return_value = False
        outer.infolist.return_value = [info]
        outer.namelist.return_value = [info.filename]
        outer.testzip.side_effect = OSError("外层压缩流校验失败")
        outer.read.return_value = b"not a nested zip"
        with (
            patch("hwtstudio.catalog.ZipFile", return_value=outer),
            patch("hwtstudio.catalog.sha256_file", return_value="0" * 64),
        ):
            catalog = scan_theme(Path("broken.hwt"))
        self.assertIn("outer_crc_check", {item["kind"] for item in catalog.warnings})

    def test_scan_detects_supported_images_by_signature_and_reports_mismatch(self):
        png = BytesIO()
        Image.new("RGBA", (8, 8), (10, 20, 30, 255)).save(png, "PNG")
        jpeg = BytesIO()
        Image.new("RGB", (8, 8), (30, 20, 10)).save(jpeg, "JPEG")
        with tempfile.TemporaryDirectory() as directory:
            module = BytesIO()
            with ZipFile(module, "w", ZIP_DEFLATED) as nested:
                nested.writestr("extensionless_icon", png.getvalue())
                nested.writestr("wrong.png", jpeg.getvalue())
            source = Path(directory) / "signature-images.hwt"
            with ZipFile(source, "w", ZIP_DEFLATED) as outer:
                outer.writestr("description.xml", b"<HwTheme/>")
                outer.writestr("icons", module.getvalue())

            catalog = scan_theme(source)
            extensionless = next(item for item in catalog.resources if item.path == "extensionless_icon")
            self.assertEqual(extensionless.resource_type, "icon")
            self.assertEqual(extensionless.actual_format, "PNG")
            mismatch = next(item for item in catalog.warnings if item["kind"] == "image_format_mismatch")
            self.assertEqual(mismatch["path"], "wrong.png")
            self.assertEqual(mismatch["expected"], "PNG")
            self.assertEqual(mismatch["actual"], "JPEG")

    def test_render_image_follows_hwt_extension_for_mismatched_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jpg"
            Image.new("RGB", (8, 8), (30, 20, 10)).save(source, "JPEG")
            slot = ResourceSlot(
                id="icons::image::wrong.png",
                module="icons",
                container="",
                resource_type="icon",
                name="wrong.png",
                path="wrong.png",
                category="桌面图标",
                label="错误扩展名",
                actual_format="JPEG",
                extension=".png",
                width=8,
                height=8,
            )
            rendered = render_image(source, slot, ResourceChange(slot_id=slot.id))
            with Image.open(BytesIO(rendered)) as image:
                self.assertEqual(image.format, "PNG")

    def test_image_loader_rejects_oversized_dimensions_before_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "declared-huge.png"
            source.write_bytes(b"not decoded")
            with patch("hwtstudio.imageops.Image.open") as open_image:
                decoded = open_image.return_value.__enter__.return_value
                decoded.width = MAX_IMAGE_DIMENSION + 1
                decoded.height = 1
                with self.assertRaisesRegex(ValueError, "单边"):
                    load_image(source)
                decoded.convert.assert_not_called()

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
