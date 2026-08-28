from __future__ import annotations

import base64
import binascii
import struct
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ANDROID_CHUNKS = {b"npTc", b"npLb", b"npOl"}


def iter_png_chunks(data: bytes):
    if not data.startswith(PNG_SIGNATURE):
        return
    pos = len(PNG_SIGNATURE)
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        yield chunk_type, payload
        pos += length + 12
        if chunk_type == b"IEND":
            break


def extract_android_chunks(data: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for chunk_type, payload in iter_png_chunks(data) or ():
        if chunk_type in ANDROID_CHUNKS:
            result[chunk_type.decode("ascii")] = base64.b64encode(payload).decode("ascii")
    return result


def inject_android_chunks(data: bytes, chunks: dict[str, str]) -> bytes:
    if not chunks or not data.startswith(PNG_SIGNATURE):
        return data
    wanted = {key.encode("ascii"): base64.b64decode(value) for key, value in chunks.items()}
    output = bytearray(PNG_SIGNATURE)
    inserted = False
    for chunk_type, payload in iter_png_chunks(data) or ():
        if chunk_type in ANDROID_CHUNKS:
            continue
        if chunk_type == b"IDAT" and not inserted:
            for key, value in wanted.items():
                output.extend(_pack_chunk(key, value))
            inserted = True
        output.extend(_pack_chunk(chunk_type, payload))
    return bytes(output)


def _pack_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = binascii.crc32(chunk_type)
    crc = binascii.crc32(payload, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def file_has_ninepatch(path: Path) -> bool:
    return "npTc" in extract_android_chunks(path.read_bytes())

