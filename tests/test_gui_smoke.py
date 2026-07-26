from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hwtstudio import __version__
from hwtstudio.app import MainWindow
from hwtstudio.models import ResourceChange, ResourceSlot, ThemeProject
from hwtstudio.semantic import SIMPLE_BY_ID
from hwtstudio.ui.dialogs import resolve_missing_assets


class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_starts_and_catalog_rebind_keeps_custom_resources(self):
        window = MainWindow()
        custom = ResourceSlot(
            id="__custom__::smoke", module="com.example", container="theme.xml", resource_type="color",
            name="smoke", path="theme.xml", category="高级自定义", label="smoke",
        )
        window.project.custom_resources.append(custom)
        window.bind_catalog(window.catalog)
        self.assertIn(custom.id, {slot.id for slot in window.resource_model.resources})
        self.assertEqual(window.windowTitle().split(" - ")[0], f"大雪主题编辑器 {__version__}")
        self.assertEqual([window.tabs.tabText(i) for i in range(window.tabs.count())], ["简洁编辑", "修改记录", "高级编辑"])
        self.assertEqual(len(window.simple_editor.cards), 30)
        self.assertTrue(window.table.isColumnHidden(2))
        self.assertTrue(window.table.isColumnHidden(5))
        window._toggle_technical_columns(True)
        self.assertFalse(window.table.isColumnHidden(2))
        window.close()

    def test_simple_group_apply_reset_and_undo(self):
        window = MainWindow()
        setting = SIMPLE_BY_ID["page_background"]
        slots = window.simple_resolved[setting.id]
        window.apply_simple_setting(setting, ResourceChange(slot_id="", value="#FF112233"))
        self.assertEqual({window.project.changes[slot.id].value for slot in slots}, {"#FF112233"})
        self.assertIn(f"涉及 {len(slots)} 个资源", window.changes_text.toPlainText())
        window.undo_stack.undo()
        self.assertFalse(any(slot.id in window.project.changes for slot in slots))
        window.undo_stack.redo()
        window.reset_simple_setting(setting)
        self.assertFalse(any(slot.id in window.project.changes for slot in slots))
        window.project.dirty = False
        window.close()

    @staticmethod
    def _missing_project(path: Path):
        slot = ResourceSlot(
            id="missing", module="com.example", container="", resource_type="image", name="photo.png",
            path="res/drawable/photo.png", category="测试", label="缺失图片", width=16, height=16,
        )
        project = ThemeProject(changes={slot.id: ResourceChange(slot_id=slot.id, source_file=str(path))})
        return project, {slot.id: slot}

    @staticmethod
    def _message_box_for(label: str):
        box = MagicMock()
        buttons = {}

        def add_button(text, _role):
            buttons[text] = object()
            return buttons[text]

        box.addButton.side_effect = add_button
        box.clickedButton.side_effect = lambda: buttons[label]
        return box

    def test_missing_asset_can_use_placeholder(self):
        project, slots = self._missing_project(Path("Z:/missing/photo.png"))
        box = self._message_box_for("使用灰白图片")
        with patch("hwtstudio.ui.dialogs.QMessageBox", return_value=box):
            self.assertTrue(resolve_missing_assets(None, project, slots))
        self.assertEqual(project.changes["missing"].source_kind, "placeholder")

    def test_missing_asset_can_be_replaced_or_cancelled(self):
        with tempfile.TemporaryDirectory() as directory:
            replacement = Path(directory) / "new.png"
            replacement.write_bytes(b"image")
            project, slots = self._missing_project(Path(directory) / "missing.png")
            box = self._message_box_for("更换新图片")
            with (
                patch("hwtstudio.ui.dialogs.QMessageBox", return_value=box),
                patch("hwtstudio.ui.dialogs.QFileDialog.getOpenFileName", return_value=(str(replacement), "")),
            ):
                self.assertTrue(resolve_missing_assets(None, project, slots))
            self.assertEqual(project.changes["missing"].source_file, str(replacement))

            cancelled, slots = self._missing_project(Path(directory) / "still-missing.png")
            box = self._message_box_for("取消打开")
            with patch("hwtstudio.ui.dialogs.QMessageBox", return_value=box):
                self.assertFalse(resolve_missing_assets(None, cancelled, slots))

    def test_missing_asset_can_search_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            found = root / "nested" / "photo.png"
            found.parent.mkdir()
            found.write_bytes(b"image")
            project, slots = self._missing_project(root / "old" / "photo.png")
            box = self._message_box_for("搜索文件夹")
            with (
                patch("hwtstudio.ui.dialogs.QMessageBox", return_value=box),
                patch("hwtstudio.ui.dialogs.QFileDialog.getExistingDirectory", return_value=str(root)),
            ):
                self.assertTrue(resolve_missing_assets(None, project, slots))
            self.assertEqual(Path(project.changes["missing"].source_file), found.resolve())


if __name__ == "__main__":
    unittest.main()
