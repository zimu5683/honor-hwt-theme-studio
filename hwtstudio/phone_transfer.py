from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import quote

from .paths import data_dir, ensure_no_symlink_parents
from .locking import interprocess_lock


PROTOCOL_VERSION = 1
FEATURE_TRANSFER_CHUNKED = "transfer_chunked"
FEATURE_TRANSFER_PREPARE = "transfer_prepare"
DISCOVERY_PORT = 48620
HTTP_PORT = 48621
DISCOVERY_REQUEST = b"HWTSTUDIO_DISCOVER_V1"
MAX_HTTP_DISCOVERY_TARGETS = 256
HTTP_DISCOVERY_WORKERS = 16
HTTP_DISCOVERY_REQUEST_TIMEOUT = 0.25
MAX_FILE_SIZE = 1024 * 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REGISTRY_BYTES = 2 * 1024 * 1024
MAX_REMOTE_TEXT_CHARS = 512
MAX_REMOTE_ERROR_CHARS = 512
MAX_FILENAME_BYTES = 200
CHUNK_SIZE = 1024 * 1024
REGISTRY_LOCK_TIMEOUT = 5.0


class PhoneTransferError(RuntimeError):
    def __init__(self, message: str, *, code: str = "transfer_failed"):
        super().__init__(message)
        self.code = code


class TransferCancelled(PhoneTransferError):
    def __init__(self):
        super().__init__("发送已取消", code="cancelled")


def _saved_text(value: object, default: str = "") -> str:
    if not isinstance(value, str):
        return default
    normalized = "".join(" " if ord(character) < 32 or ord(character) == 127 else character for character in value)
    normalized = " ".join(normalized.split())
    if not normalized:
        return default
    return normalized[:MAX_REMOTE_TEXT_CHARS]


def _saved_token(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if len(normalized) > MAX_REMOTE_TEXT_CHARS or any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return ""
    return normalized


@dataclass(slots=True)
class PhoneProfile:
    manufacturer: str = ""
    model: str = ""
    android_release: str = ""
    sdk_int: int = 0
    os_name: str = ""
    build_display: str = ""
    installed_packages: list[str] = field(default_factory=list)
    updated_at: str = ""


@dataclass(slots=True)
class PhoneDevice:
    device_id: str
    name: str
    host: str
    port: int = HTTP_PORT
    protocol: int = PROTOCOL_VERSION
    app_version: str = ""
    token: str = ""
    features: list[str] = field(default_factory=list)
    profile: PhoneProfile | None = None

    @property
    def paired(self) -> bool:
        return bool(self.token)

    @property
    def label(self) -> str:
        state = "已配对" if self.paired else "未配对"
        display_host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"{self.name}（{display_host}:{self.port}，{state}）"


class PhoneRegistry:
    """Persist paired phones with thread- and process-level serialization."""

    _lock_guard = threading.Lock()
    _locks: dict[Path, threading.RLock] = {}

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else data_dir() / "paired_phones.json"
        lock_path = self.path.absolute()
        with self._lock_guard:
            self._lock = self._locks.setdefault(lock_path, threading.RLock())

    def _validate_path(self) -> None:
        if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
            raise OSError("手机记录文件不是普通文件")
        try:
            ensure_no_symlink_parents(self.path, "手机记录文件目录不能包含符号链接")
        except ValueError as exc:
            raise OSError(str(exc)) from exc

    def load(self) -> dict[str, PhoneDevice]:
        with self._lock:
            try:
                self._validate_path()
                with _interprocess_lock(self.path):
                    return self._load_unlocked()
            except OSError:
                return {}

    def _load_unlocked(self) -> dict[str, PhoneDevice]:
        try:
            self._validate_path()
            with self.path.open("rb") as stream:
                encoded = stream.read(MAX_REGISTRY_BYTES + 1)
            if len(encoded) > MAX_REGISTRY_BYTES:
                return {}
            raw = json.loads(encoded.decode("utf-8"))
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, OSError):
            return {}
        if not isinstance(raw, dict) or not isinstance(raw.get("devices"), list):
            return {}
        devices: dict[str, PhoneDevice] = {}
        for item in raw.get("devices", []):
            if not isinstance(item, dict):
                continue
            try:
                raw_port = item.get("port", HTTP_PORT)
                if isinstance(raw_port, bool) or not isinstance(raw_port, int):
                    continue
                port = raw_port
                if not 1 <= port <= 65535:
                    continue
                raw_protocol = item.get("protocol", PROTOCOL_VERSION)
                if isinstance(raw_protocol, bool) or not isinstance(raw_protocol, int):
                    continue
                protocol = raw_protocol
                if protocol != PROTOCOL_VERSION:
                    continue
                device_id = item.get("device_id")
                host = item.get("host")
                token = item.get("token", "")
                if (
                    not isinstance(device_id, str)
                    or not device_id.strip()
                    or not isinstance(host, str)
                    or not host.strip()
                    or not isinstance(token, str)
                    or len(device_id.strip()) > MAX_REMOTE_TEXT_CHARS
                    or len(host.strip()) > MAX_REMOTE_TEXT_CHARS
                    or any(ord(character) < 32 or ord(character) == 127 for character in device_id + host)
                ):
                    continue
                profile_data = item.get("profile")
                profile = None
                if isinstance(profile_data, dict):
                    raw_sdk = profile_data.get("sdk_int", 0)
                    sdk_int = raw_sdk if isinstance(raw_sdk, int) and not isinstance(raw_sdk, bool) else 0
                    sdk_int = max(0, sdk_int)
                    packages = profile_data.get("installed_packages", [])
                    if not isinstance(packages, (list, tuple, set)):
                        packages = []
                    profile = PhoneProfile(
                        manufacturer=_saved_text(profile_data.get("manufacturer")),
                        model=_saved_text(profile_data.get("model")),
                        android_release=_saved_text(profile_data.get("android_release")),
                        sdk_int=sdk_int,
                        os_name=_saved_text(profile_data.get("os_name")),
                        build_display=_saved_text(profile_data.get("build_display")),
                        installed_packages=_payload_strings(packages),
                        updated_at=_saved_text(profile_data.get("updated_at")),
                    )
                device = PhoneDevice(
                    device_id=device_id.strip(),
                    name=_saved_text(item.get("name"), "荣耀手机") or "荣耀手机",
                    host=host.strip(),
                    port=port,
                    protocol=protocol,
                    app_version=_saved_text(item.get("app_version")),
                        token=_saved_token(token),
                    features=_payload_strings(item.get("features", [])),
                    profile=profile,
                )
            except (KeyError, TypeError, ValueError):
                continue
            if device.device_id and device.host:
                devices[device.device_id] = device
        return devices

    def save(self, devices: dict[str, PhoneDevice]) -> None:
        with self._lock:
            self._validate_path()
            with _interprocess_lock(self.path):
                self._save_unlocked(devices)

    def _save_unlocked(self, devices: dict[str, PhoneDevice]) -> None:
        self._validate_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._validate_path()
        payload = {"version": 1, "devices": [asdict(item) for item in devices.values()]}
        temp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        if temp.is_symlink() or (temp.exists() and not temp.is_file()):
            raise OSError("手机记录临时文件不是普通文件")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            if len(encoded) > MAX_REGISTRY_BYTES:
                raise ValueError("手机记录文件超过允许的大小限制")
            temp.write_bytes(encoded)
            self._validate_path()
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def update(self, device: PhoneDevice) -> None:
        with self._lock:
            self._validate_path()
            with _interprocess_lock(self.path):
                devices = self._load_unlocked()
                previous = devices.get(device.device_id)
                if (
                    previous
                    and not device.token
                    and previous.host == device.host
                    and previous.port == device.port
                ):
                    device.token = previous.token
                devices[device.device_id] = device
                self._save_unlocked(devices)

    def forget(self, device_id: str) -> None:
        with self._lock:
            self._validate_path()
            with _interprocess_lock(self.path):
                devices = self._load_unlocked()
                if devices.pop(device_id, None) is not None:
                    self._save_unlocked(devices)


@contextmanager
def _interprocess_lock(path: Path) -> Iterator[None]:
    with interprocess_lock(
        path,
        timeout=REGISTRY_LOCK_TIMEOUT,
        timeout_message="手机记录锁等待超时",
    ):
        yield


def _decode_json(data: bytes, context: str) -> dict:
    try:
        result = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhoneTransferError(f"手机返回了无法识别的{context}响应", code="bad_response") from exc
    if not isinstance(result, dict):
        raise PhoneTransferError(f"手机返回了无效的{context}响应", code="bad_response")
    return result


def _read_response(response, context: str) -> bytes:
    headers = getattr(response, "headers", {})
    raw_length = headers.get("Content-Length", "")
    if raw_length:
        try:
            declared_length = int(raw_length)
        except (TypeError, ValueError) as exc:
            raise PhoneTransferError(f"手机返回了无效的{context}响应长度", code="bad_response") from exc
    else:
        declared_length = None
    if declared_length is not None and declared_length < 0:
        raise PhoneTransferError(f"手机返回的{context}响应长度无效", code="bad_response")
    if declared_length is not None and declared_length > MAX_RESPONSE_BYTES:
        raise PhoneTransferError(f"手机返回的{context}响应过大", code="bad_response")
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise PhoneTransferError(f"手机返回的{context}响应过大", code="bad_response")
    if declared_length is not None and len(body) != declared_length:
        raise PhoneTransferError(f"手机返回的{context}响应长度与声明不一致", code="bad_response")
    return body


def _payload_protocol(payload: dict, context: str) -> int:
    value = payload.get("protocol", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PhoneTransferError(f"手机返回了无效的{context}协议版本", code="bad_response")
    return value


def _payload_text(payload: dict, key: str, context: str, *, required: bool = False, default: str = "") -> str:
    value = payload.get(key)
    if value is None and not required:
        return default
    if not isinstance(value, str) or (required and not value.strip()):
        raise PhoneTransferError(f"手机返回了无效的{context}{key}", code="bad_response")
    if len(value) > MAX_REMOTE_TEXT_CHARS or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise PhoneTransferError(f"手机返回的{context}{key}过长或包含控制字符", code="bad_response")
    normalized = value.strip()
    return normalized


def _payload_port(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PhoneTransferError(f"手机返回了无效的{context}端口", code="bad_response")
    port = value
    if not 1 <= port <= 65535:
        raise PhoneTransferError(f"手机返回了无效的{context}端口", code="bad_response")
    return port


def _payload_bool(payload: dict, key: str, context: str, *, required: bool = False, default: bool = False) -> bool:
    value = payload.get(key)
    if value is None:
        if required:
            raise PhoneTransferError(f"手机返回了无效的{context}{key}", code="bad_response")
        return default
    if not isinstance(value, bool):
        raise PhoneTransferError(f"手机返回了无效的{context}{key}", code="bad_response")
    return value


def _payload_int(payload: dict, key: str, context: str, *, minimum: int = 0) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PhoneTransferError(f"手机返回了无效的{context}{key}", code="bad_response")
    return value


def _payload_transfer_id(payload: dict, expected: str, context: str, *, required: bool = True) -> str:
    """Bind a resumable response to the request session that produced it."""
    if not required and "transfer_id" not in payload:
        return ""
    actual = _payload_text(payload, "transfer_id", context, required=True)
    if actual != expected:
        raise PhoneTransferError(f"手机返回的{context}会话标识不一致", code="bad_response")
    return actual


def _payload_strings(value: object, *, context: str = "", strict: bool = False) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        if strict:
            raise PhoneTransferError(f"手机返回了无效的{context}列表", code="bad_response")
        return []
    result = set()
    for item in value:
        if not isinstance(item, str):
            if strict:
                raise PhoneTransferError(f"手机返回了无效的{context}列表", code="bad_response")
            continue
        normalized = item.strip()
        if (
            len(normalized) > MAX_REMOTE_TEXT_CHARS
            or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        ):
            if strict:
                raise PhoneTransferError(f"手机返回了无效的{context}列表", code="bad_response")
            continue
        if normalized:
            result.add(normalized)
    return sorted(result)


def bounded_ipv4_discovery_targets(
    interfaces: list[tuple[str, str]], *, limit: int = MAX_HTTP_DISCOVERY_TARGETS,
) -> list[str]:
    """Expand only small private IPv4 networks for the bounded HTTP fallback."""
    limit = min(max(limit, 0), MAX_HTTP_DISCOVERY_TARGETS)
    if limit == 0:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for address, netmask in interfaces:
        try:
            address_value = IPv4Address(address)
            network = IPv4Network(f"{address}/{netmask}", strict=False)
        except (ValueError, TypeError):
            continue
        if not address_value.is_private or network.num_addresses > MAX_HTTP_DISCOVERY_TARGETS + 2:
            continue
        for host in network.hosts():
            value = str(host)
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
            if len(result) >= limit:
                return result
    return result


def _merge_saved(device: PhoneDevice, saved: dict[str, PhoneDevice]) -> PhoneDevice:
    previous = saved.get(device.device_id)
    if previous and previous.host == device.host and previous.port == device.port:
        device.token = previous.token
        device.profile = previous.profile
    return device


def _parse_discovered_device(
    payload: dict, host: str, default_port: int, context: str, *, require_service: bool,
) -> PhoneDevice:
    protocol = _payload_protocol(payload, context)
    if require_service and payload.get("service") != "hwtstudio":
        raise PhoneTransferError(f"手机返回了无效的{context}服务标识", code="bad_response")
    if protocol != PROTOCOL_VERSION:
        raise PhoneTransferError(f"手机返回了不兼容的{context}协议版本", code="protocol_mismatch")
    return PhoneDevice(
        device_id=_payload_text(payload, "device_id", context, required=True),
        name=_payload_text(payload, "name", context, default="荣耀手机") or "荣耀手机",
        host=host,
        port=_payload_port(payload.get("http_port", default_port), context),
        protocol=protocol,
        app_version=_payload_text(payload, "app_version", context),
        features=_payload_strings(payload.get("features", [])),
    )


def _http_discovery_probe(host: str, timeout: float) -> PhoneDevice | None:
    connection = http.client.HTTPConnection(host, HTTP_PORT, timeout=timeout)
    try:
        connection.request("GET", "/api/v1/status")
        response = connection.getresponse()
        payload = _decode_json(_read_response(response, "HTTP发现"), "HTTP发现")
        if response.status != 200:
            return None
        return _parse_discovered_device(payload, host, HTTP_PORT, "HTTP发现响应", require_service=False)
    except (OSError, http.client.HTTPException, PhoneTransferError, ValueError):
        return None
    finally:
        connection.close()


def discover_phones(timeout: float = 2.0, registry: PhoneRegistry | None = None,
                    targets: list[str] | None = None,
                    cancelled: threading.Event | None = None,
                    http_targets: list[str] | None = None) -> list[PhoneDevice]:
    registry = registry or PhoneRegistry()
    saved = registry.load()
    found: dict[str, PhoneDevice] = {}
    timeout = max(0.0, timeout)
    discovery_started = time.monotonic()
    deadline = discovery_started + timeout
    udp_deadline = discovery_started + (timeout / 2 if http_targets else timeout)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", 0))
        for target in dict.fromkeys(targets or ["255.255.255.255"]):
            if cancelled and cancelled.is_set():
                return []
            try:
                sock.sendto(DISCOVERY_REQUEST, (target, DISCOVERY_PORT))
            except OSError:
                continue
        while time.monotonic() < udp_deadline:
            if cancelled and cancelled.is_set():
                return []
            sock.settimeout(min(0.2, max(0.001, udp_deadline - time.monotonic())))
            try:
                data, address = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                raw = json.loads(data.decode("utf-8"))
                if not isinstance(raw, dict):
                    continue
                device = _parse_discovered_device(
                    raw, address[0], HTTP_PORT, "发现响应", require_service=True,
                )
            except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, PhoneTransferError):
                continue
            found[device.device_id] = _merge_saved(device, saved)
    finally:
        sock.close()

    if cancelled and cancelled.is_set():
        return []

    # UDP is the fast path. Only probe caller-supplied, validated LAN targets
    # when it produced no result, following LocalSend's legacy fallback model.
    if not found and http_targets:
        candidates: list[str] = []
        for target in dict.fromkeys(http_targets):
            try:
                address = IPv4Address(target)
            except (ValueError, TypeError):
                continue
            if address.is_private:
                candidates.append(str(address))
            if len(candidates) >= MAX_HTTP_DISCOVERY_TARGETS:
                break
        remaining = max(0.0, deadline - time.monotonic())
        if candidates and remaining > 0:
            worker_count = min(HTTP_DISCOVERY_WORKERS, len(candidates))
            executor = ThreadPoolExecutor(max_workers=worker_count)
            try:
                futures = {
                    executor.submit(
                        _http_discovery_probe,
                        target,
                        min(HTTP_DISCOVERY_REQUEST_TIMEOUT, remaining),
                    ): target
                    for target in candidates
                }
                try:
                    for future in as_completed(futures, timeout=remaining):
                        if cancelled and cancelled.is_set():
                            break
                        try:
                            device = future.result()
                        except Exception:
                            continue
                        if device is not None:
                            found[device.device_id] = _merge_saved(device, saved)
                except FuturesTimeoutError:
                    pass
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

    if cancelled and cancelled.is_set():
        return []
    for device in found.values():
        registry.update(device)
    return sorted(found.values(), key=lambda item: (item.name.casefold(), item.device_id))


def probe_phone(host: str, port: int = HTTP_PORT, timeout: float = 5.0,
                registry: PhoneRegistry | None = None,
                cancelled: threading.Event | None = None) -> PhoneDevice:
    if cancelled and cancelled.is_set():
        raise TransferCancelled()
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", "/api/v1/status")
        response = connection.getresponse()
        payload = _decode_json(_read_response(response, "状态"), "状态")
    except (OSError, http.client.HTTPException) as exc:
        raise PhoneTransferError(f"无法连接手机 {host}:{port}：{exc}", code="connect_failed") from exc
    finally:
        connection.close()
    if cancelled and cancelled.is_set():
        raise TransferCancelled()
    if response.status != 200:
        raise PhoneTransferError(_compact_remote_error(payload.get("message"), "手机接收服务不可用"), code="status_failed")
    protocol = _payload_protocol(payload, "状态")
    if protocol != PROTOCOL_VERSION:
        raise PhoneTransferError(
            f"协议版本不兼容：电脑 {PROTOCOL_VERSION}，手机 {protocol}", code="protocol_mismatch"
        )
    device = PhoneDevice(
        device_id=_payload_text(payload, "device_id", "状态响应", required=True),
        name=_payload_text(payload, "name", "状态响应", default="荣耀手机") or "荣耀手机",
        host=host,
        port=port,
        protocol=protocol,
        app_version=_payload_text(payload, "app_version", "状态响应"),
        features=_payload_strings(payload.get("features", [])),
    )
    registry = registry or PhoneRegistry()
    device = _merge_saved(device, registry.load())
    registry.update(device)
    return device


def pair_phone(device: PhoneDevice, code: str, *, client_name: str = "大雪主题编辑器",
               timeout: float = 10.0, registry: PhoneRegistry | None = None,
               cancelled: threading.Event | None = None) -> PhoneDevice:
    if cancelled and cancelled.is_set():
        raise TransferCancelled()
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
        payload = _decode_json(_read_response(response, "配对"), "配对")
    except (OSError, http.client.HTTPException) as exc:
        raise PhoneTransferError(f"配对连接失败：{exc}", code="connect_failed") from exc
    finally:
        connection.close()
    if cancelled and cancelled.is_set():
        raise TransferCancelled()
    if response.status != 200:
        raise PhoneTransferError(_compact_remote_error(payload.get("message"), "配对失败"), code="pair_failed")
    if _payload_protocol(payload, "配对") != PROTOCOL_VERSION:
        raise PhoneTransferError("手机与电脑协议版本不兼容", code="protocol_mismatch")
    token = _payload_text(payload, "token", "配对响应", required=True)
    if not token:
        raise PhoneTransferError("手机没有返回配对令牌", code="bad_response")
    remote_device_id = payload.get("device_id")
    if remote_device_id is not None:
        remote_device_id = _payload_text(payload, "device_id", "配对响应", required=True)
    paired = PhoneDevice(
        device_id=remote_device_id or device.device_id,
        name=_payload_text(payload, "name", "配对响应", default=device.name) or device.name,
        host=device.host,
        port=device.port,
        protocol=PROTOCOL_VERSION,
        app_version=_payload_text(payload, "app_version", "配对响应", default=device.app_version),
        token=token,
        features=_payload_strings(payload.get("features", device.features)),
        profile=device.profile,
    )
    (registry or PhoneRegistry()).update(paired)
    return paired


def fetch_phone_profile(device: PhoneDevice, *, timeout: float = 10.0,
                        registry: PhoneRegistry | None = None,
                        cancelled: threading.Event | None = None) -> PhoneProfile:
    if cancelled and cancelled.is_set():
        raise TransferCancelled()
    if not device.token:
        raise PhoneTransferError("手机尚未配对，无法读取适配信息", code="not_paired")
    if "device_profile" not in device.features:
        raise PhoneTransferError("手机助手版本较旧，请先更新手机助手", code="profile_unsupported")
    connection = http.client.HTTPConnection(device.host, device.port, timeout=timeout)
    try:
        connection.request("GET", "/api/v1/profile", headers={"Authorization": f"Bearer {device.token}"})
        response = connection.getresponse()
        payload = _decode_json(_read_response(response, "手机配置"), "手机配置")
    except (OSError, http.client.HTTPException) as exc:
        raise PhoneTransferError(f"无法读取手机适配信息：{exc}", code="connect_failed") from exc
    finally:
        connection.close()
    if cancelled and cancelled.is_set():
        raise TransferCancelled()
    if response.status != 200:
        raise _error_from_response(response.status, payload)
    sdk_value = payload.get("sdk_int", 0)
    if isinstance(sdk_value, bool) or not isinstance(sdk_value, int) or sdk_value < 0:
        raise PhoneTransferError("手机返回了无效的 SDK 版本", code="bad_response")
    profile = PhoneProfile(
        manufacturer=_payload_text(payload, "manufacturer", "手机配置"),
        model=_payload_text(payload, "model", "手机配置", default=device.name) or device.name,
        android_release=_payload_text(payload, "android_release", "手机配置"),
        sdk_int=sdk_value,
        os_name=_payload_text(payload, "os_name", "手机配置"),
        build_display=_payload_text(payload, "build_display", "手机配置"),
        installed_packages=_payload_strings(
            payload.get("installed_packages", []), context="已安装应用", strict=True,
        ),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    device.profile = profile
    (registry or PhoneRegistry()).update(device)
    return profile


def safe_hwt_filename(name: str) -> str:
    filename = re.sub(r"[^\w.\-]+", "_", Path(name).name, flags=re.UNICODE).strip("._")
    if not filename.lower().endswith(".hwt"):
        filename = f"{filename or 'theme'}.hwt"
    extension = filename[-4:]
    stem = filename[:-4]
    max_stem_bytes = MAX_FILENAME_BYTES - len(extension.encode("utf-8"))
    result: list[str] = []
    used_bytes = 0
    for character in stem:
        encoded = character.encode("utf-8")
        if used_bytes + len(encoded) > max_stem_bytes:
            break
        result.append(character)
        used_bytes += len(encoded)
    return "".join(result) + extension


def sha256_file(path: Path, *, cancelled: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            if cancelled and cancelled.is_set():
                raise TransferCancelled()
            digest.update(block)
    return digest.hexdigest()


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    stat = Path(path).stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _ensure_file_signature(path: Path, expected: tuple[int, int, int, int], stage: str) -> None:
    try:
        current = _file_signature(path)
    except OSError as exc:
        raise PhoneTransferError(f"主题文件在{stage}时不可用，请重新选择文件", code="file_changed") from exc
    if current != expected:
        raise PhoneTransferError(f"主题文件在{stage}时发生变化，请重新选择文件", code="file_changed")


def _ensure_file_integrity(
    path: Path,
    expected_signature: tuple[int, int, int, int],
    expected_digest: str,
    stage: str,
) -> None:
    """Confirm both filesystem identity and bytes before accepting a result."""
    _ensure_file_signature(path, expected_signature, stage)
    try:
        current_digest = sha256_file(path)
    except OSError as exc:
        raise PhoneTransferError(f"主题文件在{stage}时不可用，请重新选择文件", code="file_changed") from exc
    if current_digest.lower() != expected_digest.lower():
        raise PhoneTransferError(f"主题文件在{stage}时发生变化，请重新选择文件", code="file_changed")


def _snapshot_upload_file(
    path: Path,
    *,
    cancelled: threading.Event | None,
    progress: Callable[[int, int, str], None],
) -> tuple[tuple[int, int, int, int], str]:
    """Capture the file identity and digest used by all retries of one upload."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    initial_signature = _file_signature(path)
    size = initial_signature[2]
    if size > MAX_FILE_SIZE:
        raise PhoneTransferError("HWT 文件超过 1 GiB 上限", code="too_large")
    progress(0, size, "正在计算 SHA-256")
    try:
        digest = sha256_file(path, cancelled=cancelled)
    except OSError as exc:
        raise PhoneTransferError("主题文件在校验时不可用，请重新选择文件", code="file_changed") from exc
    _ensure_file_signature(path, initial_signature, "校验后")
    return initial_signature, digest


def _error_from_response(status: int, payload: dict) -> PhoneTransferError:
    message = _compact_remote_error(payload.get("message"), f"手机返回错误 HTTP {status}")
    codes = {
        400: "invalid_request", 401: "unauthorized", 409: "busy", 413: "too_large",
        422: "validation_failed", 507: "no_space", 503: "storage_unavailable",
    }
    return PhoneTransferError(message, code=str(payload.get("code") or codes.get(status, "transfer_failed")))


def _cancel_remote_transfer(device: PhoneDevice, transfer_id: str, *, timeout: float) -> None:
    """Best-effort cancellation for peers that implement the optional v1 extension."""
    connection = None
    try:
        connection = http.client.HTTPConnection(device.host, device.port, timeout=timeout)
        target = "/api/v1/transfers/" + quote(transfer_id, safe="")
        connection.request("DELETE", target, headers={"Authorization": f"Bearer {device.token}"})
        response = connection.getresponse()
        _read_response(response, "取消上传")
    except (OSError, http.client.HTTPException, PhoneTransferError):
        pass
    finally:
        if connection is not None:
            connection.close()


def _remote_transfer_status(device: PhoneDevice, transfer_id: str, *, timeout: float,
                            require_transfer_id: bool = False) -> dict | None:
    """Return the current state payload when the optional endpoint is supported."""
    connection = None
    try:
        connection = http.client.HTTPConnection(device.host, device.port, timeout=timeout)
        target = "/api/v1/transfers/" + quote(transfer_id, safe="")
        connection.request("GET", target, headers={"Authorization": f"Bearer {device.token}"})
        response = connection.getresponse()
        body = _read_response(response, "传输状态")
        if response.status == 404:
            return {"state": "not_found"}
        if response.status not in (200, 202):
            return None
        payload = _decode_json(body, "传输状态")
        state = _payload_text(payload, "state", "传输状态", required=True)
        if state in {"completed", "receiving", "committing"}:
            _payload_transfer_id(payload, transfer_id, "传输状态", required=require_transfer_id)
            return payload
        raise PhoneTransferError("手机返回了未知的传输状态", code="bad_response")
    except (OSError, http.client.HTTPException):
        return None
    finally:
        if connection is not None:
            connection.close()


def _wait_for_remote_commit(device: PhoneDevice, transfer_id: str, *, cancelled: threading.Event | None,
                            timeout: float, require_transfer_id: bool = False) -> dict | None:
    for _ in range(40):
        if cancelled and cancelled.is_set():
            raise TransferCancelled()
        status_payload = _remote_transfer_status(
            device,
            transfer_id,
            timeout=timeout,
            require_transfer_id=require_transfer_id,
        )
        if not status_payload or status_payload.get("state") != "committing":
            return status_payload
        if cancelled and cancelled.wait(0.25):
            raise TransferCancelled()
    raise PhoneTransferError("手机提交主题超时，请查询手机中的主题文件", code="commit_pending")


def _compact_remote_error(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = "".join(" " if ord(character) < 32 or ord(character) == 127 else character for character in value)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return fallback
    if len(cleaned) > MAX_REMOTE_ERROR_CHARS:
        return cleaned[:MAX_REMOTE_ERROR_CHARS].rstrip() + "..."
    return cleaned


def _remote_receiving_offset(payload: dict, context: str) -> tuple[int, int]:
    """Validate the byte counters used to resume a chunked transfer."""
    total = _payload_int(payload, "total", context)
    received = _payload_int(payload, "received", context)
    next_offset = _payload_int(payload, "next_offset", context)
    if received != next_offset or next_offset > total:
        raise PhoneTransferError(f"手机返回的{context}分块偏移量不一致", code="bad_response")
    return total, next_offset


def _upload_result_from_payload(payload: dict, *, path: Path, device: PhoneDevice,
                                digest: str, size: int, filename: str) -> dict:
    remote_sha = _payload_text(payload, "sha256", "上传响应", required=True).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", remote_sha):
        raise PhoneTransferError("手机返回了无效的上传 SHA-256", code="bad_response")
    if remote_sha != digest.lower():
        raise PhoneTransferError(
            f"手机校验结果不一致：本机 {digest}，手机 {remote_sha or '无结果'}", code="hash_mismatch"
        )
    remote_size = payload.get("size")
    if isinstance(remote_size, bool) or not isinstance(remote_size, int) or remote_size != size:
        raise PhoneTransferError("手机返回的上传文件大小不一致", code="bad_response")
    remote = (
        _payload_text(payload, "destination", "上传响应")
        or _payload_text(payload, "stored_name", "上传响应")
        or filename
    )
    return {
        "local": str(path),
        "remote": remote,
        "sha256": digest,
        "overwritten": _payload_bool(payload, "overwritten", "上传响应", required=True),
        "device": device.name,
        "transport": "apk",
        "theme_app_opened": _payload_bool(payload, "theme_app_opened", "上传响应", required=True),
    }


def _send_chunk(device: PhoneDevice, transfer_id: str, block: bytes, *, total_size: int,
                offset: int, digest: str, chunk_digest: str, filename: str,
                timeout: float) -> dict:
    connection = http.client.HTTPConnection(device.host, device.port, timeout=timeout)
    target = "/api/v1/transfers/" + quote(transfer_id, safe="")
    try:
        connection.putrequest("PUT", target)
        connection.putheader("Authorization", f"Bearer {device.token}")
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(len(block)))
        connection.putheader("X-Content-SHA256", digest)
        connection.putheader("X-HWT-Transfer-Id", transfer_id)
        connection.putheader("X-HWT-Total-Size", str(total_size))
        connection.putheader("X-HWT-Chunk-Offset", str(offset))
        connection.putheader("X-HWT-Chunk-SHA256", chunk_digest)
        connection.putheader("X-HWT-File-Name", quote(filename, safe=""))
        connection.endheaders()
        connection.send(block)
        response = connection.getresponse()
        payload = _decode_json(_read_response(response, "分块上传"), "分块上传")
    except (OSError, http.client.HTTPException) as exc:
        raise PhoneTransferError(f"分块上传连接中断：{exc}", code="connect_failed") from exc
    finally:
        connection.close()
    if response.status not in (200, 202):
        raise _error_from_response(response.status, payload)
    _payload_transfer_id(payload, transfer_id, "分块上传")
    return payload


def _prepare_transfer(device: PhoneDevice, transfer_id: str, *, filename: str, size: int,
                      digest: str, timeout: float) -> bool:
    connection = http.client.HTTPConnection(device.host, device.port, timeout=timeout)
    target = "/api/v1/transfers/" + quote(transfer_id, safe="") + "/prepare"
    body = json.dumps(
        {"file_name": filename, "size": size, "sha256": digest},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        connection.request(
            "POST",
            target,
            body=body,
            headers={
                "Authorization": f"Bearer {device.token}",
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        body = _read_response(response, "上传预检")
    except (OSError, http.client.HTTPException) as exc:
        raise PhoneTransferError(f"上传预检连接中断：{exc}", code="connect_failed") from exc
    finally:
        connection.close()
    if response.status == 404:
        return False
    payload = _decode_json(body, "上传预检")
    if response.status not in (200, 201):
        raise _error_from_response(response.status, payload)
    state = _payload_text(payload, "state", "上传预检", required=True)
    prepared_id = _payload_text(payload, "transfer_id", "上传预检", required=True)
    prepared_name = _payload_text(payload, "file_name", "上传预检", required=True)
    prepared_size = _payload_int(payload, "size", "上传预检")
    prepared_hash = _payload_text(payload, "sha256", "上传预检", required=True).lower()
    if (
        state != "prepared"
        or prepared_id != transfer_id
        or prepared_name != filename
        or prepared_size != size
        or prepared_hash != digest.lower()
    ):
        raise PhoneTransferError("手机返回的上传预检信息不一致", code="bad_response")
    return True


def _commit_chunk(device: PhoneDevice, transfer_id: str, *, timeout: float) -> dict:
    connection = http.client.HTTPConnection(device.host, device.port, timeout=timeout)
    target = "/api/v1/transfers/" + quote(transfer_id, safe="") + "/complete"
    try:
        connection.request(
            "POST",
            target,
            body=b"",
            headers={"Authorization": f"Bearer {device.token}", "Content-Length": "0"},
        )
        response = connection.getresponse()
        payload = _decode_json(_read_response(response, "分块提交"), "分块提交")
    except (OSError, http.client.HTTPException) as exc:
        raise PhoneTransferError(f"分块提交连接中断：{exc}", code="connect_failed") from exc
    finally:
        connection.close()
    if response.status not in (200, 201):
        raise _error_from_response(response.status, payload)
    _payload_transfer_id(payload, transfer_id, "分块提交")
    return payload


def _upload_theme_chunked(path: Path, device: PhoneDevice, *, cancelled: threading.Event | None,
                          progress: Callable[[int, int, str], None] | None,
                          timeout: float) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    initial_signature = _file_signature(path)
    size = initial_signature[2]
    if size > MAX_FILE_SIZE:
        raise PhoneTransferError("HWT 文件超过 1 GiB 上限", code="too_large")
    if not device.token:
        raise PhoneTransferError("手机尚未配对", code="not_paired")
    callback = progress or (lambda _sent, _total, _stage: None)
    transfer_id = uuid.uuid4().hex
    callback(0, size, "正在计算 SHA-256")
    try:
        digest = sha256_file(path, cancelled=cancelled)
    except OSError as exc:
        raise PhoneTransferError("主题文件在校验时不可用，请重新选择文件", code="file_changed") from exc
    _ensure_file_signature(path, initial_signature, "校验后")
    filename = safe_hwt_filename(path.name)
    if FEATURE_TRANSFER_PREPARE in device.features:
        if cancelled and cancelled.is_set():
            raise TransferCancelled()
        _prepare_transfer(
            device, transfer_id, filename=filename, size=size, digest=digest, timeout=timeout,
        )
    offset = 0
    recovery_attempts = 0
    while True:
        while offset < size:
            if cancelled and cancelled.is_set():
                _cancel_remote_transfer(device, transfer_id, timeout=min(timeout, 5.0))
                raise TransferCancelled()
            _ensure_file_signature(path, initial_signature, "发送中")
            with path.open("rb") as stream:
                stream.seek(offset)
                block = stream.read(min(CHUNK_SIZE, size - offset))
            if not block:
                raise PhoneTransferError("主题文件在分块读取时被截断，请重新选择文件", code="file_changed")
            chunk_digest = hashlib.sha256(block).hexdigest()
            try:
                payload = _send_chunk(
                    device,
                    transfer_id,
                    block,
                    total_size=size,
                    offset=offset,
                    digest=digest,
                    chunk_digest=chunk_digest,
                    filename=filename,
                    timeout=timeout,
                )
            except PhoneTransferError as exc:
                if exc.code != "connect_failed":
                    raise
                recovery_attempts += 1
                if recovery_attempts > 3:
                    raise
                if cancelled and cancelled.is_set():
                    _cancel_remote_transfer(device, transfer_id, timeout=min(timeout, 5.0))
                    raise TransferCancelled()
                status_payload = _remote_transfer_status(
                    device, transfer_id, timeout=min(timeout, 5.0), require_transfer_id=True,
                )
                if status_payload and status_payload.get("state") == "completed":
                    _ensure_file_integrity(path, initial_signature, digest, "状态确认后")
                    return _upload_result_from_payload(
                        status_payload,
                        path=path,
                        device=device,
                        digest=digest,
                        size=size,
                        filename=filename,
                    )
                if status_payload and status_payload.get("state") == "committing":
                    status_payload = _wait_for_remote_commit(
                        device,
                        transfer_id,
                        cancelled=cancelled,
                        timeout=min(timeout, 5.0),
                        require_transfer_id=True,
                    )
                    if status_payload and status_payload.get("state") == "completed":
                        _ensure_file_integrity(path, initial_signature, digest, "状态确认后")
                        return _upload_result_from_payload(
                            status_payload,
                            path=path,
                            device=device,
                            digest=digest,
                            size=size,
                            filename=filename,
                        )
                    raise PhoneTransferError("手机提交主题的状态无效", code="bad_response")
                if status_payload and status_payload.get("state") == "receiving":
                    remote_total, remote_offset = _remote_receiving_offset(status_payload, "传输状态")
                    if remote_total != size or remote_offset > size:
                        raise PhoneTransferError("手机返回了无效的分块状态", code="bad_response")
                    offset = remote_offset
                else:
                    offset = 0
                continue
            state = _payload_text(payload, "state", "分块上传", required=True)
            if state == "completed":
                _ensure_file_integrity(path, initial_signature, digest, "状态确认后")
                return _upload_result_from_payload(
                    payload,
                    path=path,
                    device=device,
                    digest=digest,
                    size=size,
                    filename=filename,
                )
            if state != "receiving":
                raise PhoneTransferError("手机返回了无效的分块状态", code="bad_response")
            remote_total = _payload_int(payload, "total", "分块上传")
            received = _payload_int(payload, "received", "分块上传")
            next_offset = _payload_int(payload, "next_offset", "分块上传")
            expected_offset = offset + len(block)
            if remote_total != size or received != expected_offset or next_offset != received:
                raise PhoneTransferError("手机返回的分块偏移量不一致", code="bad_response")
            offset = next_offset
            callback(offset, size, "正在分块发送到手机")
        if cancelled and cancelled.is_set():
            _cancel_remote_transfer(device, transfer_id, timeout=min(timeout, 5.0))
            raise TransferCancelled()
        _ensure_file_signature(path, initial_signature, "提交前")
        try:
            payload = _commit_chunk(device, transfer_id, timeout=timeout)
        except PhoneTransferError as exc:
            if exc.code != "connect_failed":
                raise
            recovery_attempts += 1
            if recovery_attempts > 3:
                raise
            if cancelled and cancelled.is_set():
                _cancel_remote_transfer(device, transfer_id, timeout=min(timeout, 5.0))
                raise TransferCancelled()
            status_payload = _remote_transfer_status(
                device, transfer_id, timeout=min(timeout, 5.0), require_transfer_id=True,
            )
            if status_payload and status_payload.get("state") == "completed":
                _ensure_file_integrity(path, initial_signature, digest, "状态确认后")
                return _upload_result_from_payload(
                    status_payload,
                    path=path,
                    device=device,
                    digest=digest,
                    size=size,
                    filename=filename,
                )
            if status_payload and status_payload.get("state") == "committing":
                status_payload = _wait_for_remote_commit(
                    device,
                    transfer_id,
                    cancelled=cancelled,
                    timeout=min(timeout, 5.0),
                    require_transfer_id=True,
                )
                if status_payload and status_payload.get("state") == "completed":
                    _ensure_file_integrity(path, initial_signature, digest, "状态确认后")
                    return _upload_result_from_payload(
                        status_payload,
                        path=path,
                        device=device,
                        digest=digest,
                        size=size,
                        filename=filename,
                    )
                raise PhoneTransferError("手机提交主题的状态无效", code="bad_response")
            if status_payload and status_payload.get("state") == "receiving":
                remote_total, offset = _remote_receiving_offset(status_payload, "传输状态")
                if remote_total != size or offset > size:
                    raise PhoneTransferError("手机返回了无效的分块状态", code="bad_response")
                continue
            offset = 0
            continue
        _ensure_file_integrity(path, initial_signature, digest, "提交响应后")
        return _upload_result_from_payload(
            payload,
            path=path,
            device=device,
            digest=digest,
            size=size,
            filename=filename,
        )


def _upload_theme_once(path: Path, device: PhoneDevice, *, transfer_id: str,
                       cancelled: threading.Event | None = None,
                       progress: Callable[[int, int, str], None] | None = None,
                       initial_signature: tuple[int, int, int, int] | None = None,
                       digest: str | None = None,
                       prepare_metadata: bool = True,
                       timeout: float = 1800.0) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    initial_signature = initial_signature or _file_signature(path)
    size = initial_signature[2]
    if size > MAX_FILE_SIZE:
        raise PhoneTransferError("HWT 文件超过 1 GiB 上限", code="too_large")
    if not device.token:
        raise PhoneTransferError("手机尚未配对", code="not_paired")
    progress = progress or (lambda _sent, _total, _stage: None)
    if digest is None:
        progress(0, size, "正在计算 SHA-256")
        try:
            digest = sha256_file(path, cancelled=cancelled)
        except OSError as exc:
            raise PhoneTransferError("主题文件在校验时不可用，请重新选择文件", code="file_changed") from exc
    _ensure_file_signature(path, initial_signature, "校验后")
    _ensure_file_signature(path, initial_signature, "发送前")
    filename = safe_hwt_filename(path.name)
    if prepare_metadata and FEATURE_TRANSFER_PREPARE in device.features:
        if cancelled and cancelled.is_set():
            raise TransferCancelled()
        _prepare_transfer(
            device, transfer_id, filename=filename, size=size, digest=digest, timeout=timeout,
        )
    target = "/api/v1/themes/" + quote(filename, safe="")
    connection = http.client.HTTPConnection(device.host, device.port, timeout=timeout)
    try:
        connection.putrequest("PUT", target)
        connection.putheader("Authorization", f"Bearer {device.token}")
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(size))
        connection.putheader("X-Content-SHA256", digest)
        connection.putheader("X-HWT-Transfer-Id", transfer_id)
        connection.endheaders()
        sent = 0
        with path.open("rb") as stream:
            remaining = size
            while remaining:
                if cancelled and cancelled.is_set():
                    raise TransferCancelled()
                block = stream.read(min(CHUNK_SIZE, remaining))
                if not block:
                    raise PhoneTransferError("主题文件在发送时被截断，请重新选择文件", code="file_changed")
                connection.send(block)
                sent += len(block)
                remaining -= len(block)
                progress(sent, size, "正在发送到手机")
        _ensure_file_signature(path, initial_signature, "发送后")
        if cancelled and cancelled.is_set():
            raise TransferCancelled()
        response = connection.getresponse()
        payload = _decode_json(_read_response(response, "上传"), "上传")
    except TransferCancelled:
        _cancel_remote_transfer(device, transfer_id, timeout=min(timeout, 5.0))
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise PhoneTransferError(f"上传连接中断：{exc}", code="connect_failed") from exc
    finally:
        connection.close()
    if response.status not in (200, 201):
        raise _error_from_response(response.status, payload)
    _ensure_file_integrity(path, initial_signature, digest, "响应确认后")
    _payload_transfer_id(payload, transfer_id, "上传响应", required=False)
    return _upload_result_from_payload(
        payload,
        path=path,
        device=device,
        digest=digest,
        size=size,
        filename=filename,
    )


def upload_theme(path: Path, device: PhoneDevice, *, cancelled: threading.Event | None = None,
                 progress: Callable[[int, int, str], None] | None = None,
                 timeout: float = 1800.0) -> dict:
    """Upload once, retrying one connection failure with the same idempotency key."""
    if FEATURE_TRANSFER_CHUNKED in device.features:
        return _upload_theme_chunked(
            path,
            device,
            cancelled=cancelled,
            progress=progress,
            timeout=timeout,
        )
    path = Path(path)
    transfer_id = uuid.uuid4().hex
    callback = progress or (lambda _sent, _total, _stage: None)
    if not path.is_file():
        raise FileNotFoundError(path)
    if not device.token:
        raise PhoneTransferError("手机尚未配对", code="not_paired")
    initial_signature, digest = _snapshot_upload_file(
        path,
        cancelled=cancelled,
        progress=callback,
    )
    try:
        return _upload_theme_once(
            path,
            device,
            transfer_id=transfer_id,
            cancelled=cancelled,
            progress=callback,
            initial_signature=initial_signature,
            digest=digest,
            timeout=timeout,
        )
    except PhoneTransferError as exc:
        if exc.code != "connect_failed":
            raise
        if cancelled and cancelled.is_set():
            _cancel_remote_transfer(device, transfer_id, timeout=min(timeout, 5.0))
            raise TransferCancelled()
        # A full PUT has no resumable offset. Wait for the original request to
        # release its session before issuing the single idempotent retry.
        for _attempt in range(40):
            status_payload = _remote_transfer_status(device, transfer_id, timeout=min(timeout, 5.0))
            if status_payload and status_payload.get("state") == "committing":
                status_payload = _wait_for_remote_commit(
                    device, transfer_id, cancelled=cancelled, timeout=min(timeout, 5.0),
                )
            if status_payload and status_payload.get("state") == "completed":
                _ensure_file_integrity(path, initial_signature, digest, "状态确认后")
                callback(initial_signature[2], initial_signature[2], "手机已完成上传，正在确认结果")
                return _upload_result_from_payload(
                    status_payload,
                    path=path,
                    device=device,
                    digest=digest,
                    size=initial_signature[2],
                    filename=safe_hwt_filename(path.name),
                )
            if not status_payload or status_payload.get("state") != "receiving":
                break
            if cancelled:
                if cancelled.wait(0.25):
                    _cancel_remote_transfer(device, transfer_id, timeout=min(timeout, 5.0))
                    raise TransferCancelled()
            else:
                time.sleep(0.25)
        callback(0, 0, "网络中断，正在重试上传")
        return _upload_theme_once(
            path,
            device,
            transfer_id=transfer_id,
            cancelled=cancelled,
            progress=callback,
            timeout=timeout,
            initial_signature=initial_signature,
            digest=digest,
            prepare_metadata=False,
        )


def transfer_to_app(path: Path, device: PhoneDevice, *, pair_code: str = "",
                    registry: PhoneRegistry | None = None, cancelled: threading.Event | None = None,
                    progress: Callable[[int, int, str], None] | None = None) -> dict:
    registry = registry or PhoneRegistry()
    if cancelled and cancelled.is_set():
        raise TransferCancelled()
    if device.device_id.startswith("manual:"):
        device = probe_phone(device.host, device.port, registry=registry, cancelled=cancelled)
    if not device.token:
        saved = registry.load().get(device.device_id)
        if saved and saved.token:
            device.token = saved.token
    if not device.token:
        device = pair_phone(device, pair_code, registry=registry, cancelled=cancelled)
    else:
        registry.update(device)
    try:
        return upload_theme(path, device, cancelled=cancelled, progress=progress)
    except PhoneTransferError as exc:
        if exc.code == "unauthorized":
            registry.forget(device.device_id)
            raise PhoneTransferError("手机已撤销配对，请重新输入配对码", code="unauthorized") from exc
        raise
