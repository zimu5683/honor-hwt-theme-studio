from __future__ import annotations

import traceback
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..ssh_transfer import transfer_to_phone
from ..phone_transfer import PhoneDevice, PhoneTransferError, TransferCancelled, transfer_to_app


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
