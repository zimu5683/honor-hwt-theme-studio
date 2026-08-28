"""纯 Python 的 bspatch 实现（兼容 bsdiff4 补丁格式）。

差分更新的客户端只负责「应用补丁」，比生成补丁简单得多，因此这里用
几十行零第三方依赖的代码实现，避免给 PyInstaller 打包引入 C 扩展。
CI 端生成补丁时使用 bsdiff4（见 tools/generate_patch.py）。

补丁格式说明（bsdiff4 的 BSDIFF40）：

    "BSDIFF40"
    int64(len_control) int64(len_diff) int64(len_new)
    bz2(control 块) bz2(diff 块) bz2(extra 块)

control 块由若干 24 字节三元组 (x, y, z) 组成，含义为：
    - x：diff 块字节数（也是本次要叠加 old 的字节数）
    - y：extra 块字节数
    - z：old 指针在 extra 之后的有符号偏移
三元组里的整数使用 bsdiff4 自定义编码：最高字节 bit7 是符号位，
其余为绝对值的低位在前（非标准二补数小端），必须用专用解码。
"""
from __future__ import annotations

import bz2

MAGIC = b"BSDIFF40"


def _decode_int64(bs8: bytes) -> int:
    """解码 bsdiff4 自定义的有符号 int64（符号位在最高字节 bit7）。"""
    value = bs8[7] & 0x7F
    for i in range(6, -1, -1):
        value = (value << 8) | bs8[i]
    if bs8[7] & 0x80:
        value = -value
    return value


def apply(old_bytes: bytes, patch_bytes: bytes) -> bytes:
    """由旧文件字节与补丁还原出新文件字节。"""
    if patch_bytes[:8] != MAGIC:
        raise ValueError("不是合法的 bsdiff 补丁")
    len_control = _decode_int64(patch_bytes[8:16])
    len_diff = _decode_int64(patch_bytes[16:24])
    len_new = _decode_int64(patch_bytes[24:32])
    pos = 32
    control = bz2.decompress(patch_bytes[pos:pos + len_control])
    pos += len_control
    diff = bz2.decompress(patch_bytes[pos:pos + len_diff])
    pos += len_diff
    extra = bz2.decompress(patch_bytes[pos:])

    old_len = len(old_bytes)
    new = bytearray()
    oldpos = 0
    diffpos = 0
    extrapos = 0
    count = len(control) // 24
    for i in range(count):
        x = _decode_int64(control[i * 24:i * 24 + 8])
        y = _decode_int64(control[i * 24 + 8:i * 24 + 16])
        z = _decode_int64(control[i * 24 + 16:i * 24 + 24])

        # diff 块：new[j] = diff[j] + old[oldpos+j]，old 越界时按 0 处理。
        diff_slice = diff[diffpos:diffpos + x]
        start = max(0, oldpos)
        end = oldpos + x
        end = min(old_len, end)
        old_slice = old_bytes[start:end] if start < end else b""
        old_slice = (
            b"\x00" * max(0, -oldpos)
            + old_slice
            + b"\x00" * max(0, (oldpos + x) - old_len)
        )
        if not diff_slice.strip(b"\x00"):
            new += old_slice
        else:
            new += bytes((a + b) & 0xFF for a, b in zip(diff_slice, old_slice, strict=False))
        diffpos += x
        oldpos += x

        # extra 块：直接拷贝。
        new += extra[extrapos:extrapos + y]
        extrapos += y
        oldpos += z

    if len(new) != len_new:
        raise ValueError(f"补丁还原长度不符：期望 {len_new}，实际 {len(new)}")
    return bytes(new)


def apply_file(old_path, patch_path, new_path) -> None:
    """把 patch 应用到 old 文件，写出 new 文件。"""
    with open(old_path, "rb") as handle:
        old_bytes = handle.read()
    with open(patch_path, "rb") as handle:
        patch_bytes = handle.read()
    new_bytes = apply(old_bytes, patch_bytes)
    with open(new_path, "wb") as handle:
        handle.write(new_bytes)
