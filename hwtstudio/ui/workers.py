from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..ssh_transfer import transfer_to_phone


class TransferWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            self.finished.emit(transfer_to_phone(self.path))
        except Exception:
            self.failed.emit(traceback.format_exc())
