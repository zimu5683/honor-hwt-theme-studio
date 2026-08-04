from __future__ import annotations

import stat
import struct
from collections import Counter
from typing import BinaryIO

from .common import normalize_archive_path


_LOCAL_FILE_HEADER = b"PK\x03\x04"
_LOCAL_FILE_HEADER_SIZE = 30


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

    for path in sorted(entries):
        parts = path.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in file_paths:
                overlaps.add((parent, path))
    return sorted(overlaps)


def _local_data_start(info, fileobj: BinaryIO | None) -> int | None:
    """Resolve a member's compressed-data start from its local file header."""
    header_offset = getattr(info, "header_offset", None)
    if isinstance(header_offset, bool) or not isinstance(header_offset, int) or header_offset < 0:
        return None

    if fileobj is not None:
        position = None
        try:
            position = fileobj.tell()
            fileobj.seek(header_offset)
            header = fileobj.read(_LOCAL_FILE_HEADER_SIZE)
            if len(header) == _LOCAL_FILE_HEADER_SIZE and header[:4] == _LOCAL_FILE_HEADER:
                filename_length, extra_length = struct.unpack_from("<HH", header, 26)
                return header_offset + _LOCAL_FILE_HEADER_SIZE + filename_length + extra_length
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        finally:
            if position is not None:
                try:
                    fileobj.seek(position)
                except (AttributeError, OSError, TypeError, ValueError):
                    pass

    filename = getattr(info, "filename", None)
    if not isinstance(filename, str):
        return None
    flag_bits = getattr(info, "flag_bits", 0)
    encoding = "utf-8" if isinstance(flag_bits, int) and flag_bits & 0x800 else "cp437"
    try:
        filename_length = len(filename.encode(encoding))
    except UnicodeEncodeError:
        filename_length = len(filename.encode("utf-8"))
    extra = getattr(info, "extra", b"")
    extra_length = len(extra) if isinstance(extra, (bytes, bytearray)) else 0
    return header_offset + _LOCAL_FILE_HEADER_SIZE + filename_length + extra_length


def archive_data_overlaps(
    infos,
    fileobj: BinaryIO | None = None,
) -> list[tuple[str, str]]:
    """Return members whose compressed data ranges physically overlap.

    ZIP central-directory records can be crafted to point at the same local
    file header or to quote another member's data.  Logical path checks do not
    catch that structure, so resolve the local header before comparing ranges.
    """
    spans: list[tuple[int, int, str]] = []
    for info in infos:
        compressed_size = getattr(info, "compress_size", None)
        if (
            isinstance(compressed_size, bool)
            or not isinstance(compressed_size, int)
            or compressed_size <= 0
        ):
            continue
        data_start = _local_data_start(info, fileobj)
        if data_start is None:
            continue
        data_end = data_start + compressed_size
        if data_end <= data_start:
            continue
        filename = getattr(info, "filename", None)
        if isinstance(filename, str):
            spans.append((data_start, data_end, filename))

    overlaps: list[tuple[str, str]] = []
    furthest_end = -1
    furthest_name = ""
    for data_start, data_end, filename in sorted(spans, key=lambda item: (item[0], item[1], item[2])):
        if data_start < furthest_end:
            overlaps.append((furthest_name, filename))
        if data_end > furthest_end:
            furthest_end = data_end
            furthest_name = filename
    return overlaps


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
