from __future__ import annotations

import hashlib
import http.client
import json
import re
import socket
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from .paths import data_dir


PROTOCOL_VERSION = 1
DISCOVERY_PORT = 48620
HTTP_PORT = 48621
DISCOVERY_REQUEST = b"HWTSTUDIO_DISCOVER_V1"
MAX_FILE_SIZE = 1024 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


class PhoneTransferError(RuntimeError):
    def __init__(self, message: str, *, code: str = "transfer_failed"):
        super().__init__(message)
        self.code = code


class TransferCancelled(PhoneTransferError):
    def __init__(self):
        super().__init__("发送已取消", code="cancelled")


@dataclass(slots=True)
class PhoneDevice:
    device_id: str
    name: str
    host: str
    port: int = HTTP_PORT
    protocol: int = PROTOCOL_VERSION
    app_version: str = ""
    token: str = ""

    @property
    def paired(self) -> bool:
        return bool(self.token)

    @property
    def label(self) -> str:
        state = "已配对" if self.paired else "未配对"
        return f"{self.name}（{self.host}:{self.port}，{state}）"


class PhoneRegistry:
    """Persist paired phones outside project files and logs."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else data_dir() / "paired_phones.json"

    def load(self) -> dict[str, PhoneDevice]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        devices: dict[str, PhoneDevice] = {}
        for item in raw.get("devices", []):
            try:
                device = PhoneDevice(
                    device_id=str(item["device_id"]),
                    name=str(item.get("name") or "荣耀手机"),
                    host=str(item.get("host") or ""),
                    port=int(item.get("port", HTTP_PORT)),
                    protocol=int(item.get("protocol", PROTOCOL_VERSION)),
                    app_version=str(item.get("app_version") or ""),
                    token=str(item.get("token") or ""),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if device.device_id:
                devices[device.device_id] = device
        return devices

    def save(self, devices: dict[str, PhoneDevice]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "devices": [asdict(item) for item in devices.values()]}
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def update(self, device: PhoneDevice) -> None:
        devices = self.load()
        previous = devices.get(device.device_id)
        if previous and not device.token:
            device.token = previous.token
        devices[device.device_id] = device
        self.save(devices)

    def forget(self, device_id: str) -> None:
        devices = self.load()
        if devices.pop(device_id, None) is not None:
            self.save(devices)


def _decode_json(data: bytes, context: str) -> dict:
    try:
        result = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhoneTransferError(f"手机返回了无法识别的{context}响应", code="bad_response") from exc
    if not isinstance(result, dict):
        raise PhoneTransferError(f"手机返回了无效的{context}响应", code="bad_response")
    return result


def _merge_saved(device: PhoneDevice, saved: dict[str, PhoneDevice]) -> PhoneDevice:
    previous = saved.get(device.device_id)
    if previous:
        device.token = previous.token
    return device


def discover_phones(timeout: float = 2.0, registry: PhoneRegistry | None = None,
                    targets: list[str] | None = None) -> list[PhoneDevice]:
    registry = registry or PhoneRegistry()
    saved = registry.load()
    found: dict[str, PhoneDevice] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        sock.settimeout(0.2)
        for target in dict.fromkeys(targets or ["255.255.255.255"]):
            try:
                sock.sendto(DISCOVERY_REQUEST, (target, DISCOVERY_PORT))
            except OSError:
                continue
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, address = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                raw = json.loads(data.decode("utf-8"))
                if raw.get("service") != "hwtstudio" or int(raw.get("protocol", 0)) != PROTOCOL_VERSION:
                    continue
                device = PhoneDevice(
                    device_id=str(raw["device_id"]),
                    name=str(raw.get("name") or "荣耀手机"),
                    host=address[0],
                    port=int(raw.get("http_port", HTTP_PORT)),
                    protocol=int(raw["protocol"]),
                    app_version=str(raw.get("app_version") or ""),
                )
            except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            found[device.device_id] = _merge_saved(device, saved)
    finally:
        sock.close()

    for device in found.values():
        registry.update(device)
    return sorted(found.values(), key=lambda item: (item.name.casefold(), item.device_id))


def probe_phone(host: str, port: int = HTTP_PORT, timeout: float = 5.0,
                registry: PhoneRegistry | None = None) -> PhoneDevice:
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", "/api/v1/status")
        response = connection.getresponse()
        payload = _decode_json(response.read(), "状态")
    except (OSError, http.client.HTTPException) as exc:
        raise PhoneTransferError(f"无法连接手机 {host}:{port}：{exc}", code="connect_failed") from exc
    finally:
        connection.close()
    if response.status != 200:
        raise PhoneTransferError(str(payload.get("message") or "手机接收服务不可用"), code="status_failed")
    protocol = int(payload.get("protocol", 0))
    if protocol != PROTOCOL_VERSION:
        raise PhoneTransferError(
            f"协议版本不兼容：电脑 {PROTOCOL_VERSION}，手机 {protocol}", code="protocol_mismatch"
        )
    device = PhoneDevice(
        device_id=str(payload["device_id"]),
        name=str(payload.get("name") or "荣耀手机"),
        host=host,
        port=port,
        protocol=protocol,
        app_version=str(payload.get("app_version") or ""),
    )
    registry = registry or PhoneRegistry()
    device = _merge_saved(device, registry.load())
    registry.update(device)
    return device


def pair_phone(device: PhoneDevice, code: str, *, client_name: str = "大雪主题编辑器",
               timeout: float = 10.0, registry: PhoneRegistry | None = None) -> PhoneDevice:
    if not re.fullmatch(r"\d{6}", code):
        raise PhoneTransferError("请输入手机显示的 6 位配对码", code="invalid_pair_code")
    body = json.dumps({"code": code, "client_name": client_name}, ensure_ascii=False).encode("utf-8")
    connection = http.client.HTTPConnection(device.host, device.port, timeout=timeout)
    try:
        connection.request(
            "POST", "/api/v1/pair", body=body,
            headers={"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(body))},
        )
        response = connection.getresponse()
        payload = _decode_json(response.read(), "配对")
    except (OSError, http.client.HTTPException) as exc:
        raise PhoneTransferError(f"配对连接失败：{exc}", code="connect_failed") from exc
    finally:
        connection.close()
    if response.status != 200:
        raise PhoneTransferError(str(payload.get("message") or "配对失败"), code="pair_failed")
    if int(payload.get("protocol", 0)) != PROTOCOL_VERSION:
        raise PhoneTransferError("手机与电脑协议版本不兼容", code="protocol_mismatch")
    token = str(payload.get("token") or "")
    if not token:
        raise PhoneTransferError("手机没有返回配对令牌", code="bad_response")
    paired = PhoneDevice(
        device_id=str(payload.get("device_id") or device.device_id),
        name=str(payload.get("name") or device.name),
        host=device.host,
        port=device.port,
        protocol=PROTOCOL_VERSION,
        app_version=str(payload.get("app_version") or device.app_version),
        token=token,
    )
    (registry or PhoneRegistry()).update(paired)
    return paired


def safe_hwt_filename(name: str) -> str:
    filename = re.sub(r"[^\w.\-]+", "_", Path(name).name, flags=re.UNICODE).strip("._")
    if not filename.lower().endswith(".hwt"):
        filename = f"{filename or 'theme'}.hwt"
    return filename


def sha256_file(path: Path, *, cancelled: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            if cancelled and cancelled.is_set():
                raise TransferCancelled()
            digest.update(block)
    return digest.hexdigest()


def _error_from_response(status: int, payload: dict) -> PhoneTransferError:
    message = str(payload.get("message") or f"手机返回错误 HTTP {status}")
    codes = {
        400: "invalid_request", 401: "unauthorized", 409: "busy", 413: "too_large",
        422: "validation_failed", 507: "no_space", 503: "storage_unavailable",
    }
    return PhoneTransferError(message, code=str(payload.get("code") or codes.get(status, "transfer_failed")))


def upload_theme(path: Path, device: PhoneDevice, *, cancelled: threading.Event | None = None,
                 progress: Callable[[int, int, str], None] | None = None,
                 timeout: float = 1800.0) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size > MAX_FILE_SIZE:
        raise PhoneTransferError("HWT 文件超过 1 GiB 上限", code="too_large")
    if not device.token:
        raise PhoneTransferError("手机尚未配对", code="not_paired")
    progress = progress or (lambda _sent, _total, _stage: None)
    progress(0, size, "正在计算 SHA-256")
    digest = sha256_file(path, cancelled=cancelled)
    filename = safe_hwt_filename(path.name)
    target = "/api/v1/themes/" + quote(filename, safe="")
    connection = http.client.HTTPConnection(device.host, device.port, timeout=timeout)
    try:
        connection.putrequest("PUT", target)
        connection.putheader("Authorization", f"Bearer {device.token}")
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(size))
        connection.putheader("X-Content-SHA256", digest)
        connection.endheaders()
        sent = 0
        with path.open("rb") as stream:
            while True:
                if cancelled and cancelled.is_set():
                    raise TransferCancelled()
                block = stream.read(CHUNK_SIZE)
                if not block:
                    break
                connection.send(block)
                sent += len(block)
                progress(sent, size, "正在发送到手机")
        response = connection.getresponse()
        payload = _decode_json(response.read(), "上传")
    except TransferCancelled:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise PhoneTransferError(f"上传连接中断：{exc}", code="connect_failed") from exc
    finally:
        connection.close()
    if response.status not in (200, 201):
        raise _error_from_response(response.status, payload)
    remote_sha = str(payload.get("sha256") or "").lower()
    if remote_sha != digest.lower():
        raise PhoneTransferError(
            f"手机校验结果不一致：本机 {digest}，手机 {remote_sha or '无结果'}", code="hash_mismatch"
        )
    return {
        "local": str(path),
        "remote": str(payload.get("destination") or payload.get("stored_name") or filename),
        "sha256": digest,
        "overwritten": bool(payload.get("overwritten", False)),
        "device": device.name,
        "transport": "apk",
        "theme_app_opened": bool(payload.get("theme_app_opened", False)),
    }


def transfer_to_app(path: Path, device: PhoneDevice, *, pair_code: str = "",
                    registry: PhoneRegistry | None = None, cancelled: threading.Event | None = None,
                    progress: Callable[[int, int, str], None] | None = None) -> dict:
    registry = registry or PhoneRegistry()
    if device.device_id.startswith("manual:"):
        device = probe_phone(device.host, device.port, registry=registry)
    if not device.token:
        saved = registry.load().get(device.device_id)
        if saved and saved.token:
            device.token = saved.token
    if not device.token:
        device = pair_phone(device, pair_code, registry=registry)
    else:
        registry.update(device)
    try:
        return upload_theme(path, device, cancelled=cancelled, progress=progress)
    except PhoneTransferError as exc:
        if exc.code == "unauthorized":
            registry.forget(device.device_id)
            raise PhoneTransferError("手机已撤销配对，请重新输入配对码", code="unauthorized") from exc
        raise
