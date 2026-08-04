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


def archive_path_overlaps(infos) -> list[tuple[str, str]]:
    """Return file/directory path prefixes that make extraction ambiguous."""
    entries: dict[str, list[object]] = {}
    for info in infos:
        filename = getattr(info, "filename", "")
        if not isinstance(filename, str):
            continue
        canonical = normalize_archive_path(filename.rstrip("/"))
        if canonical:
            entries.setdefault(canonical, []).append(info)

    overlaps: set[tuple[str, str]] = set()
    file_paths = set()
    for canonical, matching in entries.items():
        has_file = any(not info.is_dir() for info in matching)
        has_directory = any(info.is_dir() for info in matching)
        if has_file and has_directory:
            overlaps.add((canonical, canonical))
        if has_file:
            file_paths.add(canonical)

    for path in sorted(file_paths):
        parts = path.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in file_paths:
                overlaps.add((parent, path))
    return sorted(overlaps)


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
