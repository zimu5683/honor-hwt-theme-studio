"""_run 子进程管理的回归测试：管道排水与取消行为。"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import unittest

from hwtstudio.phone_transfer import TransferCancelled
from hwtstudio.ssh_transfer import _run


class SshRunTests(unittest.TestCase):
    def test_run_drains_large_output_without_false_timeout(self):
        # 输出超过管道缓冲（约 64KB）时不能因为无人读取而被误判为超时。
        size = 200 * 1024
        code = f"print('x' * {size})"
        started = time.monotonic()
        result = _run([sys.executable, "-c", code], timeout=30, cancelled=threading.Event())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), size + 1)
        self.assertLess(time.monotonic() - started, 20)

    def test_run_captures_stderr_and_check(self):
        result = _run(
            [sys.executable, "-c", "import sys; print('boom', file=sys.stderr, end='')"],
            timeout=30,
            cancelled=threading.Event(),
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "boom")
        with self.assertRaises(subprocess.CalledProcessError):
            _run([sys.executable, "-c", "raise SystemExit(3)"], timeout=30, check=True,
                 cancelled=threading.Event())

    def test_run_cancellation_terminates_child(self):
        cancelled = threading.Event()
        cancelled.set()
        started = time.monotonic()
        with self.assertRaises(TransferCancelled):
            _run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=30, cancelled=cancelled)
        self.assertLess(time.monotonic() - started, 15)

    def test_run_timeout_raises_after_deadline(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            _run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1,
                 cancelled=threading.Event())
        self.assertLess(time.monotonic() - started, 20)


if __name__ == "__main__":
    unittest.main()
