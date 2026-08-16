from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from hwtstudio.exporter import export_theme, preflight_export
from hwtstudio.models import ResourceChange, ThemeProject
from hwtstudio.semantic import (
    SIMPLE_BY_ID,
    SIMPLE_SETTINGS,
    SURFACE_TREATMENT_VALUES,
    background_setting_for_slot,
    build_surface_targets,
)
from hwtstudio.services.catalog_service import load_preferred_catalog

_BACKGROUND_SETTING_IDS = {
    "settings_background",
    "messages_background",
    "phone_background",
    "contacts_background",
}


def _background_slot(catalog, setting_id: str):
    return next(x for x in catalog.resources if x.id in SIMPLE_BY_ID[setting_id].slot_ids)


class SurfaceSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, _ = load_preferred_catalog()

    def test_four_background_settings_support_surfaces(self):
        self.assertEqual(
            _BACKGROUND_SETTING_IDS,
            {item.id for item in SIMPLE_SETTINGS if item.supports_surfaces},
        )

    def test_surface_targets_build_for_each_treatment(self):
        for setting_id in sorted(_BACKGROUND_SETTING_IDS):
            slot = _background_slot(self.catalog, setting_id)
            modules = [target["module"] for target in slot.targets]
            for treatment, expected_value in SURFACE_TREATMENT_VALUES.items():
                targets = build_surface_targets(setting_id, self.catalog, modules, treatment)
                self.assertTrue(targets, f"{setting_id}/{treatment} 没有表面目标")
                self.assertTrue(all(target["value"] == expected_value for target in targets))
                self.assertTrue(
                    all(target["resource_type"] == "color" for target in targets)
                )
                # 只包含资源目录中实际存在的颜色。
                available = {
                    (x.module, x.container, x.name)
                    for x in self.catalog.resources
                    if x.resource_type == "color"
                }
                self.assertTrue(
                    all((t["module"], t["container"], t["name"]) in available for t in targets)
                )
            self.assertEqual(
                build_surface_targets(setting_id, self.catalog, modules, "system"),
                [],
            )

    def test_background_setting_for_slot_matches_all_four(self):
        for setting_id in sorted(_BACKGROUND_SETTING_IDS):
            slot = _background_slot(self.catalog, setting_id)
            setting = background_setting_for_slot(slot)
            self.assertIsNotNone(setting)
            self.assertEqual(setting.id, setting_id)
        wallpaper = next(x for x in self.catalog.resources if x.id.startswith("__root__"))
        self.assertIsNone(background_setting_for_slot(wallpaper))

    def test_export_writes_transparent_surface_colors(self):
        slot = _background_slot(self.catalog, "contacts_background")
        project = ThemeProject(name="表面测试")
        project.set_change(
            ResourceChange(slot_id=slot.id, source_kind="placeholder", surfaces="transparent")
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "surfaces.hwt"
            _, report = export_theme(project, self.catalog, output)
            self.assertTrue(report["validation"]["valid"])
            self.assertEqual(report["preflight"]["errors"], [])
            self.assertGreater(report["preflight"]["value_targets"], 0)
            with ZipFile(output) as outer:
                with ZipFile(io.BytesIO(outer.read("com.hihonor.contacts"))) as module:
                    self.assertIn(
                        "framework-res-hnext/res/drawable-xxhdpi/background_magic.9.png",
                        module.namelist(),
                    )
                    root_xml = module.read("theme.xml").decode("utf-8")
                    fw_xml = module.read("framework-res-hnext/theme.xml").decode("utf-8")
                    self.assertIn("searchview_background_white", root_xml)
                    self.assertIn("#00000000", root_xml)
                    self.assertIn("magic_appbar_bg", fw_xml)
                    self.assertIn("magic_white_bg", fw_xml)
                    self.assertIn("#00000000", fw_xml)

    def test_export_default_frosted_surface_colors(self):
        slot = _background_slot(self.catalog, "contacts_background")
        project = ThemeProject(name="磨砂测试")
        project.set_change(ResourceChange(slot_id=slot.id, source_kind="placeholder"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "frosted.hwt"
            _, report = export_theme(project, self.catalog, output)
            self.assertTrue(report["validation"]["valid"])
            with ZipFile(output) as outer:
                with ZipFile(io.BytesIO(outer.read("com.hihonor.contacts"))) as module:
                    fw_xml = module.read("framework-res-hnext/theme.xml").decode("utf-8")
                    self.assertIn("#4DFFFFFF", fw_xml)

    def test_export_system_surfaces_writes_only_images(self):
        slot = _background_slot(self.catalog, "contacts_background")
        project = ThemeProject(name="跟随系统测试")
        project.set_change(
            ResourceChange(slot_id=slot.id, source_kind="placeholder", surfaces="system")
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "system.hwt"
            _, report = export_theme(project, self.catalog, output)
            self.assertTrue(report["validation"]["valid"])
            self.assertEqual(report["preflight"]["value_targets"], 0)
            with ZipFile(output) as outer:
                with ZipFile(io.BytesIO(outer.read("com.hihonor.contacts"))) as module:
                    self.assertNotIn("theme.xml", module.namelist())
                    self.assertIn(
                        "framework-res-hnext/res/drawable-xxhdpi/background_magic.9.png",
                        module.namelist(),
                    )

    def test_explicit_color_change_overrides_surface_default(self):
        bg_slot = _background_slot(self.catalog, "contacts_background")
        color_slot = next(
            x
            for x in self.catalog.resources
            if x.module == "com.hihonor.contacts"
            and x.container == "theme.xml"
            and x.resource_type == "color"
            and x.name == "magic_color_bg"
        )
        project = ThemeProject(name="覆盖测试")
        project.set_change(
            ResourceChange(slot_id=bg_slot.id, source_kind="placeholder", surfaces="transparent")
        )
        project.set_change(ResourceChange(slot_id=color_slot.id, value="#FF112233"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "override.hwt"
            _, report = export_theme(project, self.catalog, output)
            self.assertTrue(report["validation"]["valid"])
            self.assertTrue(
                any(
                    item["kind"] == "surface_transparency_overridden"
                    for item in report["preflight"]["warnings"]
                )
            )
            with ZipFile(output) as outer:
                with ZipFile(io.BytesIO(outer.read("com.hihonor.contacts"))) as module:
                    root_xml = module.read("theme.xml").decode("utf-8")
                    self.assertIn("#FF112233", root_xml)

    def test_legacy_project_without_surfaces_defaults_to_frosted(self):
        change = ResourceChange.from_dict({"slot_id": "x", "value": "#FF000000"})
        self.assertEqual(change.surfaces, "frosted")

    def test_preflight_reports_surface_value_targets(self):
        slot = _background_slot(self.catalog, "settings_background")
        project = ThemeProject(name="预检测试")
        project.set_change(
            ResourceChange(slot_id=slot.id, source_kind="placeholder", surfaces="frosted")
        )
        result = preflight_export(project, self.catalog)
        self.assertTrue(result["valid"])
        self.assertGreater(result["value_targets"], 0)
        self.assertGreater(result["image_targets"], 0)


if __name__ == "__main__":
    unittest.main()
