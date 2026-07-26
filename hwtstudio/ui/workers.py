from __future__ import annotations

import traceback
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..ssh_transfer import transfer_to_phone
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
    finished = Signal(object, object)
    failed = Signal(str, str)

    def __init__(self, device: PhoneDevice, pair_code: str = ""):
        super().__init__()
        self.device = device
        self.pair_code = pair_code

    def run(self):
        registry = PhoneRegistry()
        try:
            device = self.device
            if device.device_id.startswith("manual:"):
                device = probe_phone(device.host, device.port, registry=registry)
            if not device.token:
                device = pair_phone(device, self.pair_code, registry=registry)
            profile = fetch_phone_profile(device, registry=registry)
            self.finished.emit(device, profile)
        except PhoneTransferError as exc:
            self.failed.emit(str(exc), exc.code)
        except Exception:
            self.failed.emit(traceback.format_exc(), "unexpected")


class TransferWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str, str)
    progress = Signal(int, int, str)

    def __init__(self, path: Path, *, device: PhoneDevice | None = None, pair_code: str = "", use_ssh: bool = False):
        super().__init__()
        self.path = path
        self.device = device
        self.pair_code = pair_code
        self.use_ssh = use_ssh
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def _progress(self, sent: int, total: int, stage: str):
        self.progress.emit(sent, total, stage)

    def run(self):
        try:
            if self.use_ssh:
                result = transfer_to_phone(self.path)
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
            self.finished.emit(result)
        except TransferCancelled as exc:
            self.failed.emit(str(exc), exc.code)
        except PhoneTransferError as exc:
            self.failed.emit(str(exc), exc.code)
        except Exception:
            self.failed.emit(traceback.format_exc(), "unexpected")
