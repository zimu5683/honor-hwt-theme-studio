from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class InterprocessLockTimeoutError(OSError):
    """Raised when a file lock cannot be acquired within its bounded wait."""


@contextmanager
def interprocess_lock(
    path: Path,
    *,
    timeout: float,
    timeout_message: str,
) -> Iterator[None]:
    """Serialize access to the file identified by *path* across processes."""
    if timeout < 0:
        raise ValueError("锁等待时间不能为负数")
    path = Path(path).resolve()
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        locked = False
        deadline = time.monotonic() + timeout
        try:
            if os.name == "nt":
                while not locked:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise InterprocessLockTimeoutError(timeout_message) from exc
                        time.sleep(0.05)
            else:
                while not locked:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except OSError as exc:
                        if time.monotonic() >= deadline:
                            raise InterprocessLockTimeoutError(timeout_message) from exc
                        time.sleep(0.05)
            yield
        finally:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
