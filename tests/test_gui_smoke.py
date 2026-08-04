from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QThread, QTimer, Qt
from PySide6.QtWidgets import QApplication

from hwtstudio import __version__
from hwtstudio.app import MainWindow
from hwtstudio.models import ResourceChange, ResourceSlot, ThemeProject
from hwtstudio.semantic import SIMPLE_BY_ID
from hwtstudio.ui.dialogs import resolve_missing_assets
from hwtstudio.ui.design_system import Colors, STYLE_SHEET
from hwtstudio.ui.phone_dialog import PhoneTransferDialog
from hwtstudio.updater import UpdateCheck


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

    def test_operation_error_keeps_raw_exception_out_of_message_box(self):
        window = MainWindow()
        with patch("hwtstudio.app.QMessageBox.critical") as critical:
            try:
                raise RuntimeError("broken image header")
            except RuntimeError as exc:
                window._show_operation_error("预览失败", "无法生成图片预览。", "请重新选择图片后重试。", exc)
        message = critical.call_args.args[2]
        self.assertIn("无法生成图片预览", message)
        self.assertIn("处理建议", message)
        self.assertNotIn("broken image header", message)
        self.assertIn("broken image header", "\n".join(window._log_lines))
        window.close()

    def test_transfer_success_formats_structured_preflight_warnings(self):
        window = MainWindow()
        with patch("hwtstudio.app.QMessageBox.information") as information:
            window._transfer_finished(
                {
                    "remote": "Honor/Themes/theme.hwt",
                    "sha256": "a" * 64,
                    "transport": "http",
                    "theme_app_opened": False,
                    "preflight": {
                        "warnings": [
                            {"kind": "image_format_mismatch", "path": "wrong.png", "actual": "JPEG"}
                        ]
                    },
                }
            )
        message = information.call_args.args[2]
        self.assertIn("图片格式不匹配", message)
        self.assertIn("wrong.png", message)
        self.assertNotIn("{'kind'", message)
        window.close()

    def test_profile_error_detail_is_bounded_and_single_line(self):
        window = MainWindow()
        window._profile_generation = 1
        detail = "x" * 1000 + "\n第二行不应显示"
        with patch("hwtstudio.app.QMessageBox.warning") as warning:
            window._profile_failed(detail, "remote_error", 1)
        message = warning.call_args.args[2]
        self.assertLessEqual(len(message), 243)
        self.assertNotIn("第二行不应显示", message)
        window.close()

    def test_update_check_ui_callback_runs_on_gui_thread(self):
        window = MainWindow()
        result = UpdateCheck(
            current_version=__version__,
            latest_version=__version__,
            release=None,
            update_available=False,
        )
        callback_threads = []
        loop = QEventLoop()

        def show_latest(*_args):
            callback_threads.append(QThread.currentThread())
            loop.quit()

        with (
            patch("hwtstudio.ui.workers.check_for_update", return_value=result),
            patch("hwtstudio.app.QMessageBox.information", side_effect=show_latest),
        ):
            window.check_for_updates(silent=False)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()

        self.assertEqual(callback_threads, [self.app.thread()])
        window.close()

    def test_close_requests_cancellation_before_releasing_active_thread(self):
        window = MainWindow()
        window.project.dirty = False
        thread = MagicMock()
        thread.isRunning.return_value = True
        worker = MagicMock()
        window.transfer_thread = thread
        window._transfer_worker = worker

        window.close()

        worker.cancel.assert_called_once_with()
        thread.requestInterruption.assert_called_once_with()
        thread.quit.assert_called_once_with()
        self.assertTrue(window._closing)

        thread.isRunning.return_value = False
        window._transfer_thread_finished()
        self.app.processEvents()
        self.assertIsNone(window.transfer_thread)

    def test_silent_update_check_suppresses_latest_version_dialog(self):
        window = MainWindow()
        result = UpdateCheck(
            current_version=__version__,
            latest_version=__version__,
            release=None,
            update_available=False,
        )
        with patch("hwtstudio.app.QMessageBox.information") as information:
            window._update_checked(result, silent=True)
            information.assert_not_called()
            window._update_checked(result, silent=False)
            information.assert_called_once()
        window.close()

    def test_closing_window_does_not_start_update_work(self):
        window = MainWindow()
        window._closing = True
        with patch("hwtstudio.ui.workers.check_for_update") as check:
            window.check_for_updates()
            check.assert_not_called()
        self.assertIsNone(window.update_thread)
        window.close()

    def test_theme_file_dialog_remembers_portable_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            window = MainWindow()
            settings = MagicMock()
            settings.value.return_value = ""
            window.settings = settings
            window.last_export = folder / "exported.hwt"

            self.assertEqual(window._theme_file_dialog_directory(), folder)
            window._remember_theme_file_directory(folder)

            settings.setValue.assert_called_once_with("paths/theme_directory", str(folder))
            window.close()

    def test_theme_file_dialog_uses_persisted_directory_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            window = MainWindow()
            settings = MagicMock()
            settings.value.return_value = str(folder)
            window.settings = settings

            self.assertEqual(window._theme_file_dialog_directory(), folder)
            window.close()

    def test_stale_phone_discovery_completion_does_not_clear_new_thread(self):
        dialog = PhoneTransferDialog.__new__(PhoneTransferDialog)
        dialog.discovery_thread = object()
        dialog._discovery_worker = object()
        dialog._discovery_stopping = False
        dialog._discovery_generation = 2
        dialog.refresh_button = MagicMock()

        dialog._discovery_finished(1)
        self.assertIsNotNone(dialog.discovery_thread)
        dialog._discovery_finished(2)
        self.assertIsNone(dialog.discovery_thread)
        self.assertIsNone(dialog._discovery_worker)
        dialog.refresh_button.setEnabled.assert_called_once_with(True)

    def test_stale_main_window_task_callbacks_cannot_clear_new_generation(self):
        window = MainWindow()
        window._update_generation = 2
        window.update_thread = MagicMock()
        window.update_thread.isRunning.return_value = False
        window.update_worker = MagicMock()
        window._update_thread_finished(1)
        self.assertIsNotNone(window.update_thread)
        self.assertIsNotNone(window.update_worker)

        window._transfer_generation = 2
        window.transfer_thread = MagicMock()
        window.transfer_thread.isRunning.return_value = False
        window._transfer_worker = MagicMock()
        window._transfer_thread_finished(1)
        self.assertIsNotNone(window.transfer_thread)
        self.assertIsNotNone(window._transfer_worker)
        window.close()

    def test_finished_background_tasks_release_progress_dialogs(self):
        window = MainWindow()
        window._update_generation = 1
        update_progress = MagicMock()
        window.update_progress = update_progress
        with patch("hwtstudio.app.QMessageBox.information"):
            window._update_download_failed("更新下载已取消", 1)
        self.assertIsNone(window.update_progress)
        update_progress.close.assert_called_once_with()

        transfer_progress = MagicMock()
        window._transfer_generation = 1
        window.progress = transfer_progress
        with patch("hwtstudio.app.QMessageBox.information"):
            window._transfer_failed("发送已取消", "cancelled", 1)
        self.assertIsNone(window.progress)
        transfer_progress.close.assert_called_once_with()
        window.close()

    def test_phone_discovery_stop_reports_running_thread_without_destroying_it(self):
        dialog = PhoneTransferDialog.__new__(PhoneTransferDialog)
        thread = MagicMock()
        thread.isRunning.return_value = True
        worker = MagicMock()
        dialog.discovery_thread = thread
        dialog._discovery_worker = worker
        dialog._discovery_stopping = False

        self.assertFalse(dialog._finish_discovery())
        worker.cancel.assert_called_once_with()
        thread.quit.assert_called_once_with()
        thread.wait.assert_called_once_with(2500)
        self.assertIs(dialog.discovery_thread, thread)

    def test_manual_phone_address_supports_ipv6_and_rejects_bad_port(self):
        device = PhoneTransferDialog._manual_device("[fe80::1]:48622")
        self.assertEqual((device.host, device.port), ("fe80::1", 48622))
        self.assertIn("[fe80::1]:48622", device.label)

        default_port = PhoneTransferDialog._manual_device("2001:db8::1")
        self.assertEqual((default_port.host, default_port.port), ("2001:db8::1", 48621))
        with self.assertRaises(ValueError):
            PhoneTransferDialog._manual_device("[fe80::1]:invalid")
        with self.assertRaises(ValueError):
            PhoneTransferDialog._manual_device("1:2:3:bad")

    def test_studio_tokens_titlebar_and_responsive_layout(self):
        self.assertEqual(Colors.PRIMARY, "#5645D4")
        self.assertEqual(Colors.CANVAS, "#F6F5F4")
        self.assertIn("border-radius: 12px", STYLE_SHEET)
        self.assertIn("QFrame#windowTitleBar", STYLE_SHEET)
        self.assertNotIn("box-shadow", STYLE_SHEET)

        window = MainWindow()
        self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertEqual(window.title_bar.title.text().split(" - ")[0], f"大雪主题编辑器 {__version__}")
        window.show()
        for width, columns, orientation in ((1500, 3, 1), (900, 2, 2), (640, 1, 2)):
            window.resize(width, 720)
            self.app.processEvents()
            self.assertEqual(window.simple_editor._column_count, columns)
            self.assertEqual(window.resource_splitter.orientation().value, orientation)
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
