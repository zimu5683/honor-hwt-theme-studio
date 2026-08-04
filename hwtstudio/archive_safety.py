from __future__ import annotations

import stat
import struct
from collections import Counter

from .common import normalize_archive_path


def duplicate_names(infos) -> list[str]:
    counts = Counter(info.filename for info in infos)
    return sorted(name for name, count in counts.items() if count > 1)


def duplicate_normalized_names(infos) -> list[str]:
    counts = Counter(normalize_archive_path(info.filename) for info in infos)
    return sorted(name for name, count in counts.items() if count > 1)


def is_symlink(info) -> bool:
    try:
        mode = (int(info.external_attr) >> 16) & 0xFFFF
    except (TypeError, ValueError):
        return False
    return stat.S_ISLNK(mode)


def compression_ratio(info) -> float | None:
    compressed_size = getattr(info, "compress_size", None)
    file_size = getattr(info, "file_size", None)
    if not isinstance(compressed_size, int) or not isinstance(file_size, int):
        return None
    if compressed_size <= 0 or file_size <= 0:
        return None
    return file_size / compressed_size


def _zip64_values(info) -> tuple[int, ...]:
    extra = getattr(info, "extra", b"")
    if not isinstance(extra, (bytes, bytearray)):
        return ()
    offset = 0
    while offset + 4 <= len(extra):
        field_id, field_size = struct.unpack_from("<HH", extra, offset)
        offset += 4
        field_end = offset + field_size
        if field_end > len(extra):
            return ()
        if field_id == 0x0001:
            payload = extra[offset:field_end]
            if len(payload) not in {8, 16, 24}:
                return ()
            return tuple(struct.unpack_from("<" + "Q" * (len(payload) // 8), payload))
        offset = field_end
    return ()


def zip64_inconsistencies(info) -> list[str]:
    values = _zip64_values(info)
    if not values:
        return []
    issues = []
    if values[0] != info.file_size:
        issues.append(f"uncompressed_size={values[0]} (header={info.file_size})")
    if len(values) >= 2 and values[1] != info.compress_size:
        issues.append(f"compressed_size={values[1]} (header={info.compress_size})")
    return issues
