from __future__ import annotations

import traceback
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..ssh_transfer import transfer_to_phone
from ..updater import Release, check_for_update, download_asset
from ..phone_transfer import (
    PhoneDevice,
    PhoneRegistry,
    PhoneTransferError,
    TransferCancelled,
    fetch_phone_profile,
    pair_phone,
    probe_phone,
    transfer_to_app,
)


class ProfileWorker(QObject):
    finished = Signal(object, object, int)
    failed = Signal(str, str, int)

    def __init__(self, device: PhoneDevice, pair_code: str = "", *, task_id: int = 0):
        super().__init__()
        self.device = device
        self.pair_code = pair_code
        self.task_id = task_id
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def run(self):
        registry = PhoneRegistry()
        try:
            if self.cancelled.is_set():
                raise TransferCancelled()
            device = self.device
            if device.device_id.startswith("manual:"):
                device = probe_phone(device.host, device.port, registry=registry, cancelled=self.cancelled)
            if not device.token:
                device = pair_phone(device, self.pair_code, registry=registry, cancelled=self.cancelled)
            profile = fetch_phone_profile(device, registry=registry, cancelled=self.cancelled)
            self.finished.emit(device, profile, self.task_id)
        except TransferCancelled as exc:
            self.failed.emit(str(exc), exc.code, self.task_id)
        except PhoneTransferError as exc:
            self.failed.emit(str(exc), exc.code, self.task_id)
        except Exception:
            self.failed.emit(traceback.format_exc(), "unexpected", self.task_id)


class TransferWorker(QObject):
    finished = Signal(dict, int)
    failed = Signal(str, str, int)
    progress = Signal(int, int, str, int)

    def __init__(
        self,
        path: Path,
        *,
        device: PhoneDevice | None = None,
        pair_code: str = "",
        use_ssh: bool = False,
        task_id: int = 0,
    ):
        super().__init__()
        self.path = path
        self.device = device
        self.pair_code = pair_code
        self.use_ssh = use_ssh
        self.task_id = task_id
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def _progress(self, sent: int, total: int, stage: str):
        self.progress.emit(sent, total, stage, self.task_id)

    def run(self):
        try:
            if self.use_ssh:
                result = transfer_to_phone(self.path, cancelled=self.cancelled)
                result["transport"] = "ssh"
            else:
                if self.device is None:
                    raise PhoneTransferError("没有选择手机", code="no_device")
                result = transfer_to_app(
                    self.path,
                    self.device,
                    pair_code=self.pair_code,
                    cancelled=self.cancelled,
                    progress=self._progress,
                )
            self.finished.emit(result, self.task_id)
        except TransferCancelled as exc:
            self.failed.emit(str(exc), exc.code, self.task_id)
        except PhoneTransferError as exc:
            self.failed.emit(str(exc), exc.code, self.task_id)
        except Exception:
            self.failed.emit(traceback.format_exc(), "unexpected", self.task_id)


class UpdateWorker(QObject):
    """Run network and checksum work away from the Qt GUI thread."""

    checked = Signal(object, bool, int)
    check_failed = Signal(str, bool, int)
    downloaded = Signal(object, int)
    failed = Signal(str, int)
    progress = Signal(int, int, str, int)

    def __init__(self, *, release: Release | None = None, silent: bool = False, task_id: int = 0):
        super().__init__()
        self.release = release
        self.silent = silent
        self.task_id = task_id
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def run_check(self):
        try:
            self.checked.emit(check_for_update(cancelled=self.cancelled), self.silent, self.task_id)
        except Exception:
            self.check_failed.emit(traceback.format_exc(), self.silent, self.task_id)

    def run_download(self):
        try:
            if self.release is None:
                raise ValueError("请先检查更新")

            def report(received: int, total: int, stage: str):
                if self.cancelled.is_set():
                    raise RuntimeError("更新下载已取消")
                self.progress.emit(received, total, stage, self.task_id)

            self.downloaded.emit(
                download_asset(self.release, progress=report, cancelled=self.cancelled), self.task_id
            )
        except Exception:
            self.failed.emit(traceback.format_exc(), self.task_id)
