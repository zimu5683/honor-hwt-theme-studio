"""扫码直连：用电脑摄像头识别手机助手显示的二维码，免去手动输入 IP。"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtMultimedia import (
    QCamera,
    QMediaCaptureSession,
    QMediaDevices,
    QVideoFrame,
    QVideoSink,
)
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from ..phone_transfer import HTTP_PORT


LOGGER = logging.getLogger(__name__)

try:
    import zxingcpp

    ZXING_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional dependency
    zxingcpp = None
    ZXING_AVAILABLE = False


QR_PREFIXES = ("hwtstudio://", "http://")


def parse_hwt_url(url: str, default_port: int = HTTP_PORT) -> tuple[str, int] | None:
    """Parse ``hwtstudio://host[:port]`` or ``http://host[:port]``."""
    if not isinstance(url, str):
        return None
    host_port = url.split("://", 1)[-1].strip()
    if not host_port:
        return None
    port = default_port
    if host_port.startswith("["):
        closing = host_port.find("]")
        if closing <= 1:
            return None
        host = host_port[1:closing]
        suffix = host_port[closing + 1:]
        if suffix:
            if not suffix.startswith(":") or not suffix[1:].isdigit():
                return None
            port = int(suffix[1:])
    else:
        # 形如 host:port 时按最后一个冒号拆分；含多个冒号的 IPv6 视为纯地址。
        head, sep, tail = host_port.rpartition(":")
        if sep and tail.isdigit():
            host, port = head, int(tail)
        else:
            host = host_port
    if not host or not 1 <= port <= 65535:
        return None
    return host, port


def decode_qr(image: QImage) -> str | None:
    """Decode the first QR barcode in *image*, returning its text or None."""
    if not ZXING_AVAILABLE or image.isNull():
        return None
    image = image.convertToFormat(QImage.Format_RGB888)
    if image.isNull():
        return None
    from PIL import Image

    width = image.width()
    height = image.height()
    bits = image.constBits()
    if bits is None:
        return None
    payload = bytes(bits)
    # QImage 行按 4 字节对齐，bytesPerLine 可能大于 width*3，必须带上 stride。
    stride = image.bytesPerLine()
    pil_image = Image.frombytes("RGB", (width, height), payload, "raw", "RGB", stride, 0)
    try:
        result = zxingcpp.read_barcode(pil_image)
    except Exception:
        LOGGER.exception("二维码解码失败")
        return None
    return result.text if result else None


def is_hwt_qr(text: str | None) -> bool:
    return isinstance(text, str) and text.startswith(QR_PREFIXES)


class QrScanDialog(QDialog):
    """Live camera view that closes automatically once a HWT QR code is seen."""

    DECODE_INTERVAL_S = 0.15

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result_url: str | None = None
        self._decoded = False
        self._last_decode = 0.0
        self._camera: QCamera | None = None
        self._session: QMediaCaptureSession | None = None
        self._sink: QVideoSink | None = None

        self.setWindowTitle("扫码连接手机")
        self.resize(420, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        hint = QLabel(
            "在手机上打开“荣耀主题传输助手”并点击“开始接收”，\n"
            "把手机屏幕上显示的二维码对准电脑摄像头。\n"
            "识别成功后地址会自动填入，无需手动输入 IP。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.preview = QLabel("正在打开摄像头…")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(360, 300)
        self.preview.setStyleSheet("background-color: #1e1e1e; color: #cccccc;")
        layout.addWidget(self.preview)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)

        self._start_camera()

    def _start_camera(self):
        devices = QMediaDevices.videoInputs()
        if not devices:
            self.status.setText("没有检测到摄像头，无法扫码。请使用“手动地址”输入。")
            self.preview.setText("未检测到摄像头")
            return
        device = devices[0]
        self._camera = QCamera(device)
        self._session = QMediaCaptureSession()
        self._sink = QVideoSink()
        self._session.setCamera(self._camera)
        self._session.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._camera.start()

    def _stop_camera(self):
        if self._camera is not None:
            self._camera.stop()
        if self._sink is not None:
            self._sink.videoFrameChanged.disconnect(self._on_frame)

    def _on_frame(self, frame: QVideoFrame):
        if self._decoded or not ZXING_AVAILABLE:
            return
        now = time.monotonic()
        if now - self._last_decode < self.DECODE_INTERVAL_S:
            return
        self._last_decode = now
        image = frame.toImage()
        if image.isNull():
            return
        self.preview.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )
        text = decode_qr(image)
        if is_hwt_qr(text) and parse_hwt_url(text) is not None:
            self._decoded = True
            self.result_url = text
            self._stop_camera()
            self.accept()

    def reject(self):
        self._stop_camera()
        super().reject()

    def done(self, result: int):
        self._stop_camera()
        super().done(result)

    def closeEvent(self, event):
        self._stop_camera()
        super().closeEvent(event)
