from __future__ import annotations

import ipaddress
import logging
import threading

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..phone_transfer import (
    HTTP_PORT,
    PhoneDevice,
    PhoneRegistry,
    bounded_ipv4_discovery_targets,
    discover_phones,
)
from ..qr_pairing import QrPairingServer, make_qr_image, qr_connect_url
from .design_system import set_role, set_state

LOGGER = logging.getLogger(__name__)

# 总预算覆盖 UDP 广播 + 整个 /24 网段的 HTTP 兜底探测：16 个并发 ×
# 0.4 秒/请求足够在预算内扫完 254 个地址，手机 IP 偏大时也不会漏掉。
DISCOVERY_TIMEOUT = 8.0


class DiscoveryWorker(QObject):
    found = Signal(object, int)
    failed = Signal(str, int)
    finished = Signal()

    def __init__(self, registry: PhoneRegistry, *, task_id: int = 0):
        super().__init__()
        self.registry = registry
        self.task_id = task_id
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def run(self):
        try:
            targets = ["255.255.255.255"]
            interfaces: list[tuple[str, str]] = []
            for interface in QNetworkInterface.allInterfaces():
                for entry in interface.addressEntries():
                    address = entry.ip()
                    netmask = entry.netmask()
                    if (
                        address.protocol() == QAbstractSocket.IPv4Protocol
                        and netmask.protocol() == QAbstractSocket.IPv4Protocol
                    ):
                        interfaces.append((address.toString(), netmask.toString()))
                    broadcast = entry.broadcast()
                    if (
                        not broadcast.isNull()
                        and broadcast.protocol() == QAbstractSocket.IPv4Protocol
                        # /32 网卡（代理/VPN 虚拟接口）的"广播地址"就是自身
                        # 单播 IP，发往它只会产生 ICMP 错误，对发现没有帮助。
                        and netmask.protocol() == QAbstractSocket.IPv4Protocol
                        and netmask.toString() != "255.255.255.255"
                    ):
                        targets.append(broadcast.toString())
            self.found.emit(
                discover_phones(
                    registry=self.registry,
                    targets=targets,
                    http_targets=bounded_ipv4_discovery_targets(interfaces),
                    cancelled=self.cancelled,
                    timeout=DISCOVERY_TIMEOUT,
                ),
                self.task_id,
            )
        except Exception:
            LOGGER.exception("搜索手机失败")
            self.failed.emit("搜索失败", self.task_id)
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
        self._discovery_worker: DiscoveryWorker | None = None
        self._discovery_stopping = False
        self._discovery_generation = 0
        self._pending_done: int | None = None
        self._pending_close = False
        self._devices: dict[str, PhoneDevice] = self.registry.load()
        self._qr_server: QrPairingServer | None = None
        self._build_ui()
        self._render_devices()
        self.refresh()
        self._qr_server = QrPairingServer()
        self._qr_server.device_registered.connect(self._on_qr_registered)
        try:
            self._qr_server.start()
            self._refresh_qr()
        except OSError:
            self.qr_status.setText("二维码服务启动失败，请检查防火墙或换用“自动发现”。")
            set_state(self.qr_status, "warning")

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        intro = QLabel(
            "在手机上打开“荣耀主题传输助手”，点“开始接收”（首次先授权 Honor/Themes 目录）。\n"
            "首次连接输入手机屏幕上的 6 位配对码，之后无需再输。"
        )
        intro.setObjectName("infoCallout")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_discover_tab(), "自动发现")
        self.tabs.addTab(self._build_qr_tab(), "手机扫码")
        layout.addWidget(self.tabs)

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

    def _build_discover_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
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
        self.manual.setPlaceholderText("高级：通常无需填写；自动搜索不到时输入手机 IP（如 192.168.0.154，端口默认 48621）")
        form.addRow("手动地址", self.manual)
        self.code = QLineEdit()
        self.code.setPlaceholderText("已配对设备可留空")
        self.code.setMaxLength(6)
        form.addRow("配对码", self.code)
        return tab

    def _build_qr_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        hint = QLabel(
            "电脑已生成专属二维码。\n"
            "在手机上打开“荣耀主题传输助手” → 点“扫电脑码连接”，\n"
            "对准电脑屏幕扫描后，手机会自动把地址和配对码发送给电脑。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.qr_preview = QLabel("正在生成二维码…")
        self.qr_preview.setAlignment(Qt.AlignCenter)
        self.qr_preview.setMinimumSize(280, 280)
        layout.addWidget(self.qr_preview, 0, Qt.AlignCenter)
        self.qr_status = QLabel("等待手机扫码…")
        self.qr_status.setObjectName("infoCallout")
        self.qr_status.setWordWrap(True)
        layout.addWidget(self.qr_status)
        self.qr_refresh = QPushButton("刷新二维码")
        set_role(self.qr_refresh, "tertiary")
        self.qr_refresh.clicked.connect(self._refresh_qr)
        layout.addWidget(self.qr_refresh, 0, Qt.AlignCenter)
        return tab

    def _qr_hosts(self) -> list[str]:
        hosts: list[str] = []
        for interface in QNetworkInterface.allInterfaces():
            if not (interface.flags() & QNetworkInterface.IsUp):
                continue
            for entry in interface.addressEntries():
                address = entry.ip()
                if address.protocol() != QAbstractSocket.IPv4Protocol:
                    continue
                host = address.toString()
                if host == "127.0.0.1" or host.startswith("169.254."):
                    continue
                hosts.append(host)
        # 优先普通局域网（192.168.x / 10.x / 172.16-31.x），再排代理/VPN 虚拟网卡。
        def rank(host: str) -> tuple[int, str]:
            first = host.split(".", 1)[0]
            if first == "192":
                return (0, host)
            if first == "10":
                return (1, host)
            if first == "172":
                return (2, host)
            return (3, host)

        return sorted(set(hosts), key=rank)

    def _refresh_qr(self):
        if self._qr_server is None:
            self._qr_server = QrPairingServer()
            self._qr_server.device_registered.connect(self._on_qr_registered)
            self._qr_server.start()
        token = self._qr_server.new_session()
        hosts = self._qr_hosts()
        host = hosts[0] if hosts else "127.0.0.1"
        url = qr_connect_url(host, port=self._qr_server.port, session=token)
        image = make_qr_image(url, size=280)
        from PySide6.QtGui import QImage as _QImage

        data = image.tobytes()
        qimage = _QImage(data, image.size[0], image.size[1], image.size[0] * 3, _QImage.Format_RGB888).copy()
        self.qr_preview.setPixmap(QPixmap.fromImage(qimage))
        self.qr_status.setText(f"等待手机扫码…（二维码已更新）\n电脑地址：{host}:{self._qr_server.port}")

    def _on_qr_registered(self, device: PhoneDevice):
        self._devices[device.device_id] = device
        self._render_devices()
        if len(device.pair_code) == 6 and device.pair_code.isdigit():
            self.code.setText(device.pair_code)
        self.tabs.setCurrentIndex(0)
        self.qr_status.setText(f"已收到手机 {device.name} 的注册，已自动填入配对码。")
        set_state(self.qr_status, "success")
        self.status.setText(f"手机 {device.name} 已通过扫码连接，请点击“发送”。")
        set_state(self.status, "success")

    def _stop_qr_server(self):
        if self._qr_server is not None:
            self._qr_server.stop()
            self._qr_server = None

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
        if self._discovery_stopping:
            return
        if self.discovery_thread and self.discovery_thread.isRunning():
            return
        if self.discovery_thread:
            self.discovery_thread = None
            self._discovery_worker = None
        self.refresh_button.setEnabled(False)
        self.status.setText("正在搜索同一局域网内的手机……")
        self._discovery_generation += 1
        generation = self._discovery_generation
        thread = QThread(self)
        worker = DiscoveryWorker(self.registry, task_id=generation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.found.connect(self._discovery_found)
        worker.failed.connect(self._discovery_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.setProperty("hwt_generation", generation)
        thread.finished.connect(self._discovery_finished)
        thread.finished.connect(thread.deleteLater)
        self.discovery_thread = thread
        self._discovery_worker = worker
        self._discovery_stopping = False
        thread.start()

    def _discovery_found(self, devices: list[PhoneDevice], generation: int):
        if self._discovery_stopping or generation != self._discovery_generation:
            return
        for device in devices:
            self._devices[device.device_id] = device
        self._render_devices()
        if devices:
            self.status.setText(f"发现 {len(devices)} 台正在接收的手机。")
            set_state(self.status, "success")
        else:
            if self.devices.count():
                self.status.setText(
                    "本次搜索没有发现正在接收的手机；如果列表中显示了已保存的手机，"
                    "可直接选中后点击“发送”，或先在手机上重新点“开始接收”。"
                )
            else:
                self.status.setText("没有发现手机。请确认 APK 已开始接收，或填写手动地址。")
            set_state(self.status, "warning")

    def _discovery_failed(self, _message: str, generation: int):
        if self._discovery_stopping or generation != self._discovery_generation:
            return
        self.status.setText("搜索失败，请检查网络连接后重试。")
        self.status.setToolTip("")
        set_state(self.status, "error")

    def _discovery_finished(self, generation: int | None = None):
        if generation is None:
            sender = self.sender()
            generation = sender.property("hwt_generation") if sender is not None else None
        if generation != self._discovery_generation or self.discovery_thread is None:
            return
        self.discovery_thread = None
        self._discovery_worker = None
        if self._discovery_stopping:
            if self._pending_close:
                self._pending_close = False
                QTimer.singleShot(0, self.close)
            elif self._pending_done is not None:
                result = self._pending_done
                self._pending_done = None
                QTimer.singleShot(0, lambda result=result: self.done(result))
            return
        self.refresh_button.setEnabled(True)

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
        if not value:
            raise ValueError("手动地址格式不正确")
        host = value
        port = HTTP_PORT
        if value.startswith("["):
            closing = value.find("]")
            if closing <= 1:
                raise ValueError("手动地址格式不正确")
            host = value[1:closing]
            suffix = value[closing + 1:]
            if suffix:
                if not suffix.startswith(":") or not suffix[1:].isdigit():
                    raise ValueError("手动地址格式不正确")
                port = int(suffix[1:])
            try:
                ipaddress.IPv6Address(host)
            except ValueError as exc:
                raise ValueError("手动地址格式不正确") from exc
        elif ":" in value:
            if value.count(":") == 1:
                maybe_host, maybe_port = value.rsplit(":", 1)
                if not maybe_host or not maybe_port.isdigit():
                    raise ValueError("手动地址格式不正确")
                host, port = maybe_host, int(maybe_port)
            else:
                try:
                    ipaddress.IPv6Address(value)
                except ValueError as exc:
                    raise ValueError("手动地址格式不正确") from exc
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
                # 组合框里的对象可能是搜索开始前加载的旧记录，而
                # 发现/扫码回调已经把新对象（实时 feature、最新地址）
                # 写进 _devices；这里取最新的一份，避免用过期的
                # feature 或端口去发送。
                device = self._devices.get(device.device_id, device)
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
        self._stop_qr_server()
        if not self._finish_discovery():
            self._pending_done = None
            self._pending_close = True
            self.refresh_button.setEnabled(False)
            self.send_button.setEnabled(False)
            self.ssh_button.setEnabled(False)
            event.ignore()
            return
        super().closeEvent(event)

    def done(self, result):
        self._stop_qr_server()
        if not self._finish_discovery():
            self._pending_done = result
            self._pending_close = False
            self.refresh_button.setEnabled(False)
            self.send_button.setEnabled(False)
            self.ssh_button.setEnabled(False)
            return
        super().done(result)

    def _finish_discovery(self):
        self._discovery_stopping = True
        if not self.discovery_thread:
            return True
        thread = self.discovery_thread
        worker = self._discovery_worker
        if worker is not None:
            worker.cancel()
        if thread.isRunning():
            thread.quit()
            thread.wait(2500)
        if not thread.isRunning():
            self.discovery_thread = None
            self._discovery_worker = None
            return True
        return False
