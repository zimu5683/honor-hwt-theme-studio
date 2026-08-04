from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QColorDialog, QMessageBox, QPushButton

from hwtstudio.models import ResourceChange, ResourceSlot, ThemeProject
from hwtstudio.semantic import SIMPLE_BY_ID, SIMPLE_SETTINGS
from hwtstudio.ui.dialogs import RESOURCE_TYPE_LABELS, CustomResourceDialog
from hwtstudio.ui.i18n import install_qt_translations
from hwtstudio.ui.simple_editor import SimpleSettingCard
from hwtstudio.ui.simple_preview import PreviewRepository


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        install_qt_translations(cls.app)
        cls.repository = PreviewRepository()

    def test_all_thirty_settings_have_real_scene_targets(self):
        self.assertTrue(self.repository.available)
        self.assertEqual(len(SIMPLE_SETTINGS), 30)
        for setting in SIMPLE_SETTINGS:
            self.assertIsNotNone(setting.preview, setting.id)
            scene = self.repository.scene(setting.preview)
            self.assertIsNotNone(scene, setting.id)
            self.assertIn(setting.preview.target, scene.targets, setting.id)
            self.assertTrue(self.repository.highlighted_image(setting.preview), setting.id)

    def test_uploaded_scene_targets_match_current_phone_layout(self):
        volume = self.repository.scenes["volume_overlay"].targets
        self.assertEqual(volume["panel"], (0.06, 0.20, 0.94, 0.55))
        self.assertEqual(volume["slider"], (0.11, 0.26, 0.89, 0.50))
        quick = self.repository.scenes["quick_settings"].targets
        self.assertEqual(quick["brightness"], (0.52, 0.26, 0.69, 0.43))
        notification = self.repository.scenes["notification_shade"].targets
        self.assertEqual(notification["icon"], (0.78, 0.10, 0.92, 0.18))
        wechat = self.repository.scenes["wechat_settings"].targets
        self.assertEqual(wechat["brand"], (0.05, 0.92, 0.19, 1.0))
        self.assertEqual(SIMPLE_BY_ID["bottom_bar"].preview.scene, "wechat_settings")
        self.assertEqual(SIMPLE_BY_ID["bottom_bar"].preview.target, "bottom")

    def test_alpha_color_changes_target_only(self):
        spec = next(item.preview for item in SIMPLE_SETTINGS if item.id == "page_background")
        assert spec is not None
        base = self.repository.base_image(spec)
        current = self.repository.current_image(spec, ResourceChange(slot_id="x", value="#80FF0000"))
        assert base is not None and current is not None
        self.assertNotEqual(base.getpixel((base.width // 2, base.height // 2)), current.getpixel((current.width // 2, current.height // 2)))
        self.assertEqual(base.getpixel((1, 1)), current.getpixel((1, 1)))

    def test_image_fit_and_missing_states_are_rendered(self):
        spec = next(item.preview for item in SIMPLE_SETTINGS if item.id == "messages_background")
        assert spec is not None
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "source.png"
            Image.new("RGBA", (10, 30), (20, 80, 220, 255)).save(image_path)
            for fit in ("cover", "contain", "stretch"):
                rendered = self.repository.current_image(
                    spec,
                    ResourceChange(slot_id="x", source_file=str(image_path), fit=fit, enhance="light", enhance_strength=0.2),
                )
                self.assertIsNotNone(rendered)
            missing = self.repository.current_image(
                spec,
                ResourceChange(slot_id="x", source_file=str(Path(directory) / "missing.png")),
            )
            self.assertIsNotNone(missing)

    def test_to_pixmap_preserves_rgba_pixels_without_file_round_trip(self):
        image = Image.new("RGBA", (3, 2), (12, 34, 56, 78))
        pixmap = PreviewRepository.to_pixmap(image)
        self.assertEqual((pixmap.width(), pixmap.height()), (3, 2))
        color = pixmap.toImage().pixelColor(0, 0)
        self.assertEqual((color.red(), color.green(), color.blue(), color.alpha()), (12, 34, 56, 78))

    @staticmethod
    def _preview_slots() -> list[ResourceSlot]:
        return [
            ResourceSlot(
                id=f"preview-{index}", module="framework-res", container="theme.xml", resource_type="color",
                name="magic_color_bg", path="theme.xml", category="测试", label="测试",
            )
            for index in range(2)
        ]

    def test_card_and_dialog_preview_share_uniform_compatible_change(self):
        setting = SIMPLE_BY_ID["page_background"]
        slots = self._preview_slots()
        change = ResourceChange(slot_id="", value="#80123456")
        project = ThemeProject(changes={slot.id: ResourceChange(slot_id=slot.id, value=change.value) for slot in slots})
        card = SimpleSettingCard(setting, lambda *_args: None, lambda *_args: None, self.repository)
        with patch.object(self.repository, "current_image", wraps=self.repository.current_image) as current:
            card.bind(slots, project)
            current.assert_called_once()
            self.assertEqual(current.call_args.args[1].value, change.value)
        with patch("hwtstudio.ui.simple_editor.PreviewDialog") as dialog:
            card._show_preview()
            args, kwargs = dialog.call_args
            self.assertEqual(args[2].value, change.value)
            self.assertFalse(kwargs["mixed"])
        card.close()

    def test_dialog_does_not_pick_arbitrary_change_for_mixed_resources(self):
        setting = SIMPLE_BY_ID["page_background"]
        slots = self._preview_slots()
        project = ThemeProject(changes={
            slots[0].id: ResourceChange(slot_id=slots[0].id, value="#80123456"),
            slots[1].id: ResourceChange(slot_id=slots[1].id, value="#80654321"),
        })
        card = SimpleSettingCard(setting, lambda *_args: None, lambda *_args: None, self.repository)
        card.bind(slots, project)
        with patch("hwtstudio.ui.simple_editor.PreviewDialog") as dialog:
            card._show_preview()
            args, kwargs = dialog.call_args
            self.assertIsNone(args[2])
            self.assertTrue(kwargs["mixed"])
        card.close()

    def test_advanced_type_labels_keep_raw_values(self):
        dialog = CustomResourceDialog()
        self.assertEqual(dialog.kind.count(), len(RESOURCE_TYPE_LABELS))
        for index in range(dialog.kind.count()):
            self.assertNotIn(dialog.kind.itemText(index), RESOURCE_TYPE_LABELS)
            self.assertIn(dialog.kind.itemData(index), RESOURCE_TYPE_LABELS)
        self.assertEqual(dialog.kind.itemText(0), "颜色")
        dialog.close()

    def test_qt_standard_dialogs_are_chinese(self):
        color = QColorDialog()
        color.show()
        self.app.processEvents()
        button_texts = {button.text() for button in color.findChildren(QPushButton)}
        self.assertIn("确定", button_texts)
        self.assertIn("取消", button_texts)
        self.assertIn("拾取屏幕颜色(&P)", button_texts)
        color.close()
        message = QMessageBox()
        message.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        message.show()
        self.app.processEvents()
        self.assertEqual({button.text() for button in message.buttons()}, {"是(&Y)", "否(&N)"})
        message.close()


if __name__ == "__main__":
    unittest.main()
