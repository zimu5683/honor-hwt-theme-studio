from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..phone_transfer import HTTP_PORT, PhoneDevice, PhoneRegistry, discover_phones
from .design_system import set_role, set_state


class DiscoveryWorker(QObject):
    found = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, registry: PhoneRegistry):
        super().__init__()
        self.registry = registry

    def run(self):
        try:
            targets = ["255.255.255.255"]
            for interface in QNetworkInterface.allInterfaces():
                for entry in interface.addressEntries():
                    broadcast = entry.broadcast()
                    if not broadcast.isNull() and broadcast.protocol() == QAbstractSocket.IPv4Protocol:
                        targets.append(broadcast.toString())
            self.found.emit(discover_phones(registry=self.registry, targets=targets))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()


class PhoneTransferDialog(QDialog):
    """Choose a discovered receiver, or opt into the legacy SSH path."""

    def __init__(self, parent=None, registry: PhoneRegistry | None = None, purpose: str = "send"):
        super().__init__(parent)
        self.setWindowTitle("发送到荣耀主题传输助手")
        self.resize(620, 280)
        self.registry = registry or PhoneRegistry()
        self.device: PhoneDevice | None = None
        self.pair_code = ""
        self.use_ssh = False
        self.purpose = purpose
        self.discovery_thread: QThread | None = None
        self._devices: dict[str, PhoneDevice] = self.registry.load()
        self._build_ui()
        self._render_devices()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        intro = QLabel(
            "请先在手机打开“荣耀主题传输助手”，授权 Honor/Themes 后点击“开始接收”。\n"
            "首次连接输入手机显示的 6 位配对码，后续会自动记住。"
        )
        intro.setObjectName("infoCallout")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        device_row = QHBoxLayout()
        self.devices = QComboBox()
        device_row.addWidget(self.devices, 1)
        self.refresh_button = QPushButton("刷新")
        set_role(self.refresh_button, "tertiary")
        self.refresh_button.clicked.connect(self.refresh)
        device_row.addWidget(self.refresh_button)
        self.forget_button = QPushButton("忘记配对")
        set_role(self.forget_button, "danger")
        self.forget_button.clicked.connect(self.forget_selected)
        device_row.addWidget(self.forget_button)
        form.addRow("发现的手机", device_row)

        self.manual = QLineEdit()
        self.manual.setPlaceholderText("可选，例如 10.71.175.15 或 10.71.175.15:48621")
        form.addRow("手动地址", self.manual)
        self.code = QLineEdit()
        self.code.setPlaceholderText("已配对设备可留空")
        self.code.setMaxLength(6)
        form.addRow("配对码", self.code)
        layout.addLayout(form)

        self.status = QLabel("正在搜索同一局域网内的手机……")
        self.status.setObjectName("infoCallout")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.send_button = buttons.addButton("识别手机" if self.purpose == "profile" else "发送", QDialogButtonBox.AcceptRole)
        self.ssh_button = buttons.addButton("使用 Termux/SSH 备用", QDialogButtonBox.ActionRole)
        set_role(self.send_button, "primary")
        set_role(self.ssh_button, "secondary")
        self.ssh_button.setVisible(self.purpose == "send")
        self.send_button.clicked.connect(self.accept_phone)
        self.ssh_button.clicked.connect(self.accept_ssh)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _render_devices(self):
        current_id = None
        current = self.devices.currentData()
        if isinstance(current, PhoneDevice):
            current_id = current.device_id
        self.devices.clear()
        for device in sorted(self._devices.values(), key=lambda item: (item.name.casefold(), item.device_id)):
            self.devices.addItem(device.label, device)
            if device.device_id == current_id:
                self.devices.setCurrentIndex(self.devices.count() - 1)
        self.forget_button.setEnabled(self.devices.count() > 0)

    def refresh(self):
        if self.discovery_thread and self.discovery_thread.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.status.setText("正在搜索同一局域网内的手机……")
        thread = QThread(self)
        worker = DiscoveryWorker(self.registry)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.found.connect(self._discovery_found)
        worker.failed.connect(self._discovery_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._discovery_finished)
        thread.finished.connect(thread.deleteLater)
        self.discovery_thread = thread
        self._discovery_worker = worker
        thread.start()

    def _discovery_found(self, devices: list[PhoneDevice]):
        for device in devices:
            self._devices[device.device_id] = device
        self._render_devices()
        if devices:
            self.status.setText(f"发现 {len(devices)} 台正在接收的手机。")
            set_state(self.status, "success")
        else:
            self.status.setText("没有发现手机。请确认 APK 已开始接收，或填写手动地址。")
            set_state(self.status, "warning")

    def _discovery_failed(self, message: str):
        self.status.setText(f"搜索失败：{message}")
        set_state(self.status, "error")

    def _discovery_finished(self):
        self.refresh_button.setEnabled(True)
        self.discovery_thread = None

    def forget_selected(self):
        device = self.devices.currentData()
        if not isinstance(device, PhoneDevice):
            return
        if QMessageBox.question(self, "忘记配对", f"确定忘记 {device.name} 的配对信息吗？") != QMessageBox.Yes:
            return
        self.registry.forget(device.device_id)
        self._devices.pop(device.device_id, None)
        self._render_devices()

    @staticmethod
    def _manual_device(value: str) -> PhoneDevice:
        value = value.strip()
        host = value
        port = HTTP_PORT
        if value.count(":") == 1:
            maybe_host, maybe_port = value.rsplit(":", 1)
            if maybe_port.isdigit():
                host, port = maybe_host, int(maybe_port)
        if not host or not 1 <= port <= 65535:
            raise ValueError("手动地址格式不正确")
        return PhoneDevice(device_id=f"manual:{host}:{port}", name="手动连接的荣耀手机", host=host, port=port)

    def accept_phone(self):
        try:
            if self.manual.text().strip():
                device = self._manual_device(self.manual.text())
            else:
                device = self.devices.currentData()
                if not isinstance(device, PhoneDevice):
                    raise ValueError("请选择发现的手机，或填写手动地址")
            code = self.code.text().strip()
            if not device.paired and not (len(code) == 6 and code.isdigit()):
                raise ValueError("首次连接请输入手机显示的 6 位配对码")
        except ValueError as exc:
            QMessageBox.warning(self, "无法发送", str(exc))
            return
        self.device = device
        self.pair_code = code
        self.accept()

    def accept_ssh(self):
        self.use_ssh = True
        self.accept()

    def closeEvent(self, event):
        self._finish_discovery()
        super().closeEvent(event)

    def done(self, result):
        self._finish_discovery()
        super().done(result)

    def _finish_discovery(self):
        if self.discovery_thread and self.discovery_thread.isRunning():
            self.discovery_thread.quit()
            self.discovery_thread.wait(2500)
