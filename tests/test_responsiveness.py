from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from hwtstudio.app import MainWindow, transfer_error_message
from hwtstudio.imageops import PREVIEW_MAX_EDGE, load_image_preview
from hwtstudio.models import ResourceChange, ThemeProject
from hwtstudio.phone_transfer import PhoneDevice
from hwtstudio.semantic import PreviewSpec
from hwtstudio.services.catalog_service import load_preferred_catalog
from hwtstudio.ui.simple_preview import PreviewRepository
from hwtstudio.ui.workers import ExportWorker, TransferWorker


class PreviewImageTests(unittest.TestCase):
    def test_large_jpeg_preview_is_capped(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "huge.jpg"
            Image.new("RGB", (4000, 3000), (90, 60, 40)).save(source, "JPEG", quality=90)
            image = load_image_preview(source)
            self.assertLessEqual(max(image.width, image.height), PREVIEW_MAX_EDGE)
            # 细节要求低,但内容不能丢:仍然是 4:3 形状。
            self.assertAlmostEqual(image.width / image.height, 4 / 3, delta=0.02)

    def test_small_image_preview_keeps_size(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "small.png"
            Image.new("RGBA", (64, 32), (1, 2, 3, 4)).save(source)
            image = load_image_preview(source)
            self.assertEqual(image.size, (64, 32))


class PreviewCacheTests(unittest.TestCase):
    def _repository(self, directory: Path) -> tuple[PreviewRepository, PreviewSpec]:
        scene_png = Path(directory) / "scene.png"
        Image.new("RGBA", (600, 400), (200, 200, 200, 255)).save(scene_png)
        manifest = {
            "device": {"model": "测试机", "magic_os": "MagicOS 10"},
            "note": "测试场景",
            "scenes": {
                "scene": {
                    "file": scene_png.name,
                    "width": 600,
                    "height": 400,
                    "targets": {"content": [0.0, 0.0, 1.0, 1.0]},
                }
            },
        }
        (Path(directory) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        repository = PreviewRepository(directory)
        spec = PreviewSpec("scene", "content", "测试场景内容")
        return repository, spec

    def test_composite_is_cached_until_change_or_file_differs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wall.png"
            Image.new("RGB", (3000, 2000), (10, 120, 200)).save(source)
            repository, spec = self._repository(root)
            change = ResourceChange(slot_id="x", source_file=str(source))
            first = repository.current_image(spec, change)
            self.assertIsNotNone(first)
            second = repository.current_image(spec, change)
            self.assertIs(first, second)  # 命中缓存,不重复合成
            # 适配方式变化后缓存失效。
            other = ResourceChange(slot_id="x", source_file=str(source), fit="stretch")
            third = repository.current_image(spec, other)
            self.assertIsNot(first, third)
            again = repository.current_image(spec, other)
            self.assertIs(third, again)
            # 同一路径下图片内容变化(时间戳/大小变化)后缓存失效。
            Image.new("RGB", (900, 600), (200, 10, 120)).save(source)
            fresh_change = ResourceChange(slot_id="x", source_file=str(source))
            fourth = repository.current_image(spec, fresh_change)
            self.assertIsNot(third, fourth)


class TransferMessageTests(unittest.TestCase):
    def test_known_codes_have_actionable_messages(self):
        message = transfer_error_message("connect_failed", "无法连接手机 10.0.0.2:48621：timed out")
        self.assertIn("开始接收", message)
        self.assertIn("10.0.0.2", message)
        self.assertEqual(transfer_error_message("no_device", ""), "没有选择手机，请先连接并识别手机。")

    def test_upload_interrupted_message_is_actionable_and_keeps_detail(self):
        message = transfer_error_message(
            "upload_interrupted", "上传连接中断：[WinError 10054] 远程主机强迫关闭了一个现有的连接。"
        )
        self.assertIn("接收中", message)
        self.assertIn("10054", message)
        self.assertNotIn("无法连接手机。", message)

    def test_unknown_code_falls_back_to_generic_with_detail(self):
        message = transfer_error_message("mystery_code", "手机返回错误 HTTP 500")
        self.assertIn("发送失败", message)
        self.assertIn("手机返回错误 HTTP 500", message)


class TransferWorkerLiveProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_saved_device_uses_live_probe_features_for_upload(self):
        worker = TransferWorker(
            Path("theme.hwt"),
            device=PhoneDevice("phone-1", "旧记录", "10.0.0.8", token="token", features=[]),
        )
        live = PhoneDevice(
            "phone-1", "实时手机", "10.0.0.8", token="token", features=["transfer_chunked", "transfer_prepare"],
        )
        with (
            patch("hwtstudio.ui.workers.probe_phone", return_value=live) as probe,
            patch("hwtstudio.ui.workers.transfer_to_app", return_value={"remote": "Honor/Themes/theme.hwt"}) as transfer,
        ):
            worker.run()

        probe.assert_called_once()
        uploaded_device = transfer.call_args.args[1]
        self.assertEqual(uploaded_device.features, ["transfer_chunked", "transfer_prepare"])


class ExportWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.catalog, _ = load_preferred_catalog()

    def _run_worker(self, project: ThemeProject, output: Path) -> tuple[list, list]:
        worker = ExportWorker(project, self.catalog, output, task_id=1)
        finished: list = []
        failed: list = []
        worker.finished.connect(lambda report, generation: finished.append(report), Qt.ConnectionType.DirectConnection)
        worker.failed.connect(lambda message, generation: failed.append(message), Qt.ConnectionType.DirectConnection)
        worker.run()
        return finished, failed

    def test_export_worker_emits_report(self):
        slot = next(x for x in self.catalog.resources if x.id == "__synthetic__::background::联系人背景")
        project = ThemeProject(name="后台导出")
        project.set_change(ResourceChange(slot_id=slot.id, source_kind="placeholder", surfaces="frosted"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "background.hwt"
            finished, failed = self._run_worker(project, output)
            self.assertFalse(failed)
            self.assertEqual(len(finished), 1)
            report = finished[0]
            self.assertTrue(report["validation"]["valid"])
            self.assertTrue(output.is_file())

    def test_export_worker_reports_failure(self):
        project = ThemeProject(name="失败导出")
        project.set_change(
            ResourceChange(slot_id="__synthetic__::background::联系人背景", source_file=str(Path("missing.png")))
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "broken.hwt"
            finished, failed = self._run_worker(project, output)
            self.assertFalse(finished)
            self.assertEqual(len(failed), 1)
            self.assertIn("missing_image", failed[0])
            self.assertFalse(output.is_file())


class AsyncExportGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.catalog, _ = load_preferred_catalog()

    def test_async_export_runs_off_the_gui_thread_and_reports(self):
        window = MainWindow()
        slot = next(x for x in self.catalog.resources if x.id == "__synthetic__::background::联系人背景")
        window.project.set_change(ResourceChange(slot_id=slot.id, source_kind="placeholder", surfaces="frosted"))
        try:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "async.hwt"
                with (
                    patch("hwtstudio.app.QMessageBox.information"),
                    patch("hwtstudio.app.QMessageBox.critical"),
                ):
                    window._start_export(output)
                    self.assertTrue(window.export_thread.isRunning())
                    loop = QEventLoop()
                    window._export_worker.finished.connect(loop.quit)
                    window._export_worker.failed.connect(loop.quit)
                    QTimer.singleShot(60000, loop.quit)
                    loop.exec()
                    self.assertTrue(output.is_file())
                    self.assertEqual(window.last_export, output)
                    self.assertIsNone(window.export_progress)
        finally:
            with patch("hwtstudio.app.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                window.close()


if __name__ == "__main__":
    unittest.main()
