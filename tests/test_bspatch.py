"""bspatch（差分更新应用端）回归测试。

生成端在 CI 使用 bsdiff4；这里按 BSDIFF40 格式手工构造小补丁，
覆盖自定义 int64 解码、control/diff/extra 三块的处理路径。
"""
from __future__ import annotations

import bz2
import tempfile
import unittest
from pathlib import Path

from hwtstudio.bspatch import apply, apply_file

MAGIC = b"BSDIFF40"


def _encode_int64(value: int) -> bytes:
    """按 bsdiff4 自定义编码写出有符号 int64（符号位在最高字节 bit7）。"""
    negative = value < 0
    magnitude = -value if negative else value
    out = bytearray(8)
    for index in range(7):
        out[index] = magnitude & 0xFF
        magnitude >>= 8
    out[7] = (magnitude & 0x7F) | (0x80 if negative else 0)
    return bytes(out)


def _build_patch(old: bytes, new: bytes) -> bytes:
    """对 old→new 构造单三元组补丁：前 len(old) 字节用 diff 叠加，其余进 extra。"""
    common = min(len(old), len(new))
    diff = bytes((n - o) & 0xFF for o, n in zip(old[:common], new[:common], strict=True))
    diff = diff + b"\x00" * max(0, len(new) - common)
    extra = new[common:]
    if len(new) > len(old):
        diff = diff[: len(old)]
    control = _encode_int64(common) + _encode_int64(len(extra)) + _encode_int64(0)
    control_block = bz2.compress(control)
    diff_block = bz2.compress(diff)
    extra_block = bz2.compress(extra)
    header = (
        MAGIC
        + _encode_int64(len(control_block))
        + _encode_int64(len(diff_block))
        + _encode_int64(len(new))
    )
    return header + control_block + diff_block + extra_block


class BspatchTests(unittest.TestCase):
    def test_apply_reconstructs_identical_content(self):
        old = b"the quick brown fox"
        self.assertEqual(apply(old, _build_patch(old, old)), old)

    def test_apply_reconstructs_appended_bytes(self):
        old = b"AAAA" * 8
        new = old + b"appended-tail"
        self.assertEqual(apply(old, _build_patch(old, new)), new)

    def test_apply_reconstructs_modified_bytes(self):
        old = bytes(range(256))
        new = bytes((value * 7 + 3) & 0xFF for value in old)
        self.assertEqual(apply(old, _build_patch(old, new)), new)

    def test_apply_supports_truncated_output(self):
        old = b"0123456789"
        new = b"01234"
        self.assertEqual(apply(old, _build_patch(old, new)), new)

    def test_apply_rejects_bad_magic(self):
        with self.assertRaisesRegex(ValueError, "不是合法的 bsdiff 补丁"):
            apply(b"old", b"NOTDIFF!" + b"\x00" * 24)

    def test_apply_rejects_length_mismatch(self):
        old = b"0123456789"
        patch = _build_patch(old, b"01234")
        # 篡改头部声明的还原长度，应用端必须拒绝而不是静默截断/补零。
        tampered = patch[:24] + _encode_int64(len(old)) + patch[32:]
        with self.assertRaisesRegex(ValueError, "补丁还原长度不符"):
            apply(old, tampered)

    def test_apply_file_writes_reconstructed_output(self):
        old = b"file based old content"
        new = old + b" + tail"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_path = root / "old.bin"
            patch_path = root / "patch.bin"
            new_path = root / "new.bin"
            old_path.write_bytes(old)
            patch_path.write_bytes(_build_patch(old, new))
            apply_file(old_path, patch_path, new_path)
            self.assertEqual(new_path.read_bytes(), new)


if __name__ == "__main__":
    unittest.main()
