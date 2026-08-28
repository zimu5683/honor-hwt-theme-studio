from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import Future, ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from hwtstudio.phone_transfer import (
    CHUNK_SIZE,
    DISCOVERY_REQUEST,
    FEATURE_TRANSFER_CHUNKED,
    FEATURE_TRANSFER_PARALLEL,
    FEATURE_TRANSFER_PREPARE,
    MAX_FILENAME_BYTES,
    MAX_REGISTRY_BYTES,
    MAX_REMOTE_ERROR_CHARS,
    MAX_REMOTE_TEXT_CHARS,
    MAX_RESPONSE_BYTES,
    PhoneDevice,
    PhoneProfile,
    PhoneRegistry,
    PhoneTransferError,
    TransferCancelled,
    _error_from_response,
    _http_discovery_candidates,
    _interprocess_lock,
    _merge_saved,
    bounded_ipv4_discovery_targets,
    discover_phones,
    fetch_phone_profile,
    pair_phone,
    probe_phone,
    safe_hwt_filename,
    sha256_file,
    transfer_to_app,
    upload_theme,
)


class ReceiverHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    received = b""

    def log_message(self, _format, *_args):
        pass

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/v1/profile":
            if self.headers.get("Authorization") != "Bearer test-token":
                self._json(401, {"message": "未授权", "code": "unauthorized"})
                return
            self._json(200, {
                "manufacturer": "HONOR", "model": "ELP-AN00", "android_release": "16",
                "sdk_int": 36, "os_name": "MagicOS_10.0.0", "build_display": "test",
                "installed_packages": ["com.android.settings", "com.tencent.mm"],
            })
            return
        self._json(200, {
            "protocol": 1,
            "device_id": "phone-1",
            "name": "测试手机",
            "app_version": "0.1.0",
            "features": ["device_profile"],
        })

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body.get("code") != "123456":
            self._json(401, {"message": "配对码错误"})
            return
        self._json(200, {
            "protocol": 1,
            "device_id": "phone-1",
            "name": "测试手机",
            "token": "test-token",
            "app_version": "0.1.0",
            "features": ["device_profile"],
        })

    def do_PUT(self):
        if self.headers.get("Authorization") != "Bearer test-token":
            self._json(401, {"message": "未授权", "code": "unauthorized"})
            return
        type(self).received = self.rfile.read(int(self.headers["Content-Length"]))
        digest = hashlib.sha256(type(self).received).hexdigest()
        self._json(201, {
            "stored_name": "theme.hwt",
            "destination": "Honor/Themes/theme.hwt",
            "size": len(type(self).received),
            "sha256": digest,
            "overwritten": False,
            "theme_app_opened": False,
        })


class FakeHttpResponse:
    def __init__(self, payload: dict, status: int = 200, headers: dict | None = None):
        self.status = status
        self.headers = headers or {}
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, size: int = -1):
        return self._body if size < 0 else self._body[:size]


class FakeHttpConnection:
    response_payload: ClassVar[dict] = {}
    response_status: ClassVar[int] = 200
    response_headers: ClassVar[dict] = {}

    def __init__(self, *_args, **_kwargs):
        pass

    def request(self, *_args, **_kwargs):
        pass

    def putrequest(self, *_args, **_kwargs):
        pass

    def putheader(self, *_args, **_kwargs):
        pass

    def endheaders(self):
        pass

    def send(self, _data):
        pass

    def getresponse(self):
        return FakeHttpResponse(
            type(self).response_payload,
            type(self).response_status,
            type(self).response_headers,
        )

    def close(self):
        pass


class ChunkedConnection:
    plans: ClassVar[list] = []
    instances: ClassVar[list] = []

    def __init__(self, *_args, **_kwargs):
        self.headers = {}
        self.method = None
        self.target = None
        self.body = bytearray()
        type(self).instances.append(self)

    def putrequest(self, method, target):
        self.method = method
        self.target = target

    def putheader(self, name, value):
        self.headers[name] = value

    def endheaders(self):
        pass

    def send(self, data):
        self.body.extend(data)

    def request(self, method, target, body=None, headers=None):
        self.method = method
        self.target = target
        self.headers.update(headers or {})
        if body:
            self.body.extend(body)

    def getresponse(self):
        # Each HTTP request consumes the next plan entry. Chunked uploads now
        # reuse one connection across chunks, so plans must advance per request
        # (or per getresponse) instead of per connection construction.
        try:
            self.plan = type(self).plans.pop(0)
        except IndexError:
            raise AssertionError("计划序列已耗尽") from None
        if isinstance(self.plan, BaseException):
            raise self.plan
        status, payload = self.plan
        if isinstance(payload, dict) and payload.get("transfer_id") == "session":
            payload = dict(payload)
            marker = "/api/v1/transfers/"
            transfer_id = self.target.split(marker, 1)[1].split("/", 1)[0]
            payload["transfer_id"] = transfer_id
        elif (
            isinstance(payload, dict)
            and self.method == "GET"
            and payload.get("state") == "completed"
            and "transfer_id" not in payload
        ) or (
            isinstance(payload, dict)
            and self.method == "POST"
            and self.target.endswith("/complete")
            and "transfer_id" not in payload
        ):
            payload = dict(payload)
            marker = "/api/v1/transfers/"
            payload["transfer_id"] = self.target.split(marker, 1)[1].split("/", 1)[0]
        return FakeHttpResponse(payload, status=status)

    def close(self):
        pass


class FakeDiscoverySocket:
    def __init__(self, *_args, **_kwargs):
        self.sent = False
        self.returned = False

    def setsockopt(self, *_args):
        pass

    def bind(self, _address):
        pass

    def settimeout(self, _timeout):
        pass

    def sendto(self, data, _address):
        self.sent = data == DISCOVERY_REQUEST

    def recvfrom(self, _size):
        if self.sent and not self.returned:
            self.returned = True
            body = json.dumps({
                "service": "hwtstudio",
                "protocol": 1,
                "device_id": "phone-1",
                "name": "荣耀测试机",
                "http_port": 48621,
                "app_version": "0.1.0",
                "features": ["device_profile"],
            }).encode()
            return body, ("10.0.0.8", 48620)
        raise TimeoutError()

    def close(self):
        pass


class InvalidPortDiscoverySocket(FakeDiscoverySocket):
    def recvfrom(self, _size):
        if self.sent and not self.returned:
            self.returned = True
            body = json.dumps({
                "service": "hwtstudio",
                "protocol": 1,
                "device_id": "phone-invalid-port",
                "name": "异常手机",
                "http_port": 70000,
            }).encode()
            return body, ("10.0.0.9", 48620)
        raise TimeoutError()


class MissingDeviceIdDiscoverySocket(FakeDiscoverySocket):
    def recvfrom(self, _size):
        if self.sent and not self.returned:
            self.returned = True
            body = json.dumps({
                "service": "hwtstudio",
                "protocol": 1,
                "name": "异常手机",
                "http_port": 48621,
            }).encode()
            return body, ("10.0.0.10", 48620)
        raise TimeoutError()


class InvalidShapeDiscoverySocket(FakeDiscoverySocket):
    def recvfrom(self, _size):
        if self.sent and not self.returned:
            self.returned = True
            return b"[]", ("10.0.0.11", 48620)
        raise TimeoutError()


class SilentDiscoverySocket(FakeDiscoverySocket):
    def recvfrom(self, _size):
        raise TimeoutError()


class HttpDiscoveryConnection:
    calls: ClassVar[list] = []

    def __init__(self, host, port, timeout):
        type(self).calls.append((host, port, timeout))
        self.host = host

    def request(self, *_args, **_kwargs):
        pass

    def getresponse(self):
        if self.host != "10.0.0.8":
            return FakeHttpResponse({"message": "not a receiver"}, status=404)
        return FakeHttpResponse({
            "protocol": 1,
            "device_id": "http-phone-1",
            "name": "HTTP 发现手机",
            "app_version": "0.2.0",
            "features": ["device_profile"],
        })

    def close(self):
        pass


class InvalidHttpDiscoveryConnection(HttpDiscoveryConnection):
    def getresponse(self):
        return FakeHttpResponse({"protocol": 1, "name": "缺少设备标识"})


class SerialExecutor:
    """串行执行已提交任务，使并行上传测试的计划序列保持确定。"""

    def __init__(self, max_workers: int = 1):
        self.max_workers = max_workers

    def submit(self, function, *args, **kwargs):
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class PhoneTransferTests(unittest.TestCase):
    def test_sha256_file_reports_incremental_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.hwt"
            content = b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE + b"tail"
            path.write_bytes(content)
            progress = []

            digest = sha256_file(path, progress=lambda completed, total: progress.append((completed, total)))

            self.assertEqual(digest, hashlib.sha256(content).hexdigest())
            self.assertEqual(
                progress,
                [
                    (0, len(content)),
                    (CHUNK_SIZE, len(content)),
                    (CHUNK_SIZE * 2, len(content)),
                    (len(content), len(content)),
                ],
            )

    def test_saved_credentials_are_bound_to_the_identified_endpoint(self):
        profile = PhoneProfile(model="已确认手机")
        saved = {
            "phone-1": PhoneDevice(
                "phone-1",
                "已配对手机",
                "10.0.0.8",
                port=48621,
                token="saved-token",
                profile=profile,
            )
        }

        same_endpoint = _merge_saved(
            PhoneDevice("phone-1", "发现手机", "10.0.0.8", port=48621),
            saved,
        )
        self.assertEqual(same_endpoint.token, "saved-token")
        self.assertIs(same_endpoint.profile, profile)

        moved_endpoint = _merge_saved(
            PhoneDevice("phone-1", "冒用手机", "10.0.0.9", port=48621),
            saved,
        )
        self.assertEqual(moved_endpoint.token, "")
        self.assertIsNone(moved_endpoint.profile)

        moved_port = _merge_saved(
            PhoneDevice("phone-1", "冒用手机", "10.0.0.8", port=48622),
            saved,
        )
        self.assertEqual(moved_port.token, "")
        self.assertIsNone(moved_port.profile)

        with tempfile.TemporaryDirectory() as directory:
            registry = PhoneRegistry(Path(directory) / "phones.json")
            registry.update(saved["phone-1"])
            registry.update(moved_endpoint)
            self.assertEqual(registry.load()["phone-1"].token, "")

    def test_chunked_upload_sends_offsets_and_both_sha256_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "分块主题.hwt"
            first = b"a" * CHUNK_SIZE
            second = b"b" * CHUNK_SIZE
            content = first + second
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (202, {
                    "state": "receiving", "transfer_id": "session", "received": CHUNK_SIZE,
                    "total": len(content), "next_offset": CHUNK_SIZE,
                }),
                (202, {
                    "state": "receiving", "transfer_id": "session", "received": len(content),
                    "total": len(content), "next_offset": len(content),
                }),
                (201, {
                    "stored_name": "分块主题.hwt", "destination": "Honor/Themes/分块主题.hwt",
                    "size": len(content), "sha256": digest, "overwritten": False,
                    "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection):
                progress = []
                result = upload_theme(
                    path,
                    device,
                    progress=lambda completed, total, stage: progress.append((completed, total, stage)),
                )

            self.assertEqual(result["sha256"], digest)
            hash_progress = [
                (completed, total)
                for completed, total, stage in progress
                if stage == "正在校验文件完整性（SHA-256）"
            ]
            self.assertEqual(hash_progress[0], (0, len(content)))
            self.assertEqual(hash_progress[-1], (len(content), len(content)))
            self.assertGreater(len(hash_progress), 2)
            self.assertIn((0, 0, "正在准备发送到手机"), progress)
            # 两块分块在同一 HTTP 连接上连续发出（keep-alive 复用），
            # 提交请求使用独立连接。
            self.assertEqual(len(ChunkedConnection.instances), 2)
            upload_connection, commit_request = ChunkedConnection.instances
            self.assertEqual(upload_connection.method, "PUT")
            self.assertEqual(bytes(upload_connection.body), content)
            self.assertEqual(bytes(upload_connection.body)[:CHUNK_SIZE], first)
            self.assertEqual(bytes(upload_connection.body)[CHUNK_SIZE:], second)
            self.assertEqual(upload_connection.headers["X-HWT-Chunk-Offset"], str(CHUNK_SIZE))
            self.assertEqual(upload_connection.headers["X-HWT-Chunk-SHA256"], hashlib.sha256(second).hexdigest())
            self.assertEqual(upload_connection.headers["X-Content-SHA256"], digest)
            self.assertEqual(upload_connection.headers["X-HWT-Total-Size"], str(len(content)))
            self.assertEqual(upload_connection.headers["X-HWT-File-Name"], "%E5%88%86%E5%9D%97%E4%B8%BB%E9%A2%98.hwt")
            self.assertEqual(commit_request.method, "POST")
            self.assertTrue(commit_request.target.endswith("/complete"))

    def test_chunked_upload_recovers_from_lost_chunk_response_using_remote_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "恢复主题.hwt"
            content = b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                OSError("第一块响应前断开"),
                (202, {
                    "state": "receiving", "transfer_id": "session", "received": CHUNK_SIZE,
                    "total": len(content), "next_offset": CHUNK_SIZE,
                }),
                (202, {
                    "state": "receiving", "transfer_id": "session", "received": len(content),
                    "total": len(content), "next_offset": len(content),
                }),
                (201, {
                    "stored_name": "恢复主题.hwt", "destination": "Honor/Themes/恢复主题.hwt",
                    "size": len(content), "sha256": digest, "overwritten": False,
                    "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection):
                result = upload_theme(path, device)

            self.assertEqual(result["sha256"], digest)
            self.assertEqual([request.method for request in ChunkedConnection.instances], ["PUT", "GET", "PUT", "POST"])
            self.assertEqual(ChunkedConnection.instances[2].headers["X-HWT-Chunk-Offset"], str(CHUNK_SIZE))
            self.assertEqual(ChunkedConnection.instances[2].body, content[CHUNK_SIZE:])

    def test_chunked_upload_rejects_inconsistent_remote_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "异常恢复主题.hwt"
            content = b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE
            path.write_bytes(content)
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                OSError("第一块响应前断开"),
                (202, {
                    "state": "receiving", "transfer_id": "session",
                    "received": CHUNK_SIZE - 1, "total": len(content),
                    "next_offset": CHUNK_SIZE,
                }),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection), self.assertRaisesRegex(PhoneTransferError, "偏移量不一致") as raised:
                upload_theme(path, device)

            self.assertEqual(raised.exception.code, "bad_response")
            self.assertEqual([request.method for request in ChunkedConnection.instances], ["PUT", "GET"])

    def test_chunked_upload_rejects_mismatched_response_transfer_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "串会话主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (202, {
                    "state": "receiving", "transfer_id": "another-session",
                    "received": len(content), "total": len(content), "next_offset": len(content),
                }),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection), self.assertRaisesRegex(PhoneTransferError, "会话标识不一致") as raised:
                upload_theme(path, device)

            self.assertEqual(raised.exception.code, "bad_response")
            self.assertEqual(len(ChunkedConnection.instances), 1)

    def test_chunked_recovery_rejects_mismatched_status_transfer_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "串会话恢复.hwt"
            content = b"payload"
            path.write_bytes(content)
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                OSError("上传响应前断开"),
                (202, {
                    "state": "receiving", "transfer_id": "another-session",
                    "received": len(content), "total": len(content), "next_offset": len(content),
                }),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection), self.assertRaisesRegex(PhoneTransferError, "会话标识不一致") as raised:
                upload_theme(path, device)

            self.assertEqual(raised.exception.code, "bad_response")
            self.assertEqual([request.method for request in ChunkedConnection.instances], ["PUT", "GET"])

    def test_chunked_commit_rejects_mismatched_response_transfer_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "串会话提交.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (202, {
                    "state": "receiving", "transfer_id": "session",
                    "received": len(content), "total": len(content), "next_offset": len(content),
                }),
                (201, {
                    "transfer_id": "another-session", "stored_name": "串会话提交.hwt",
                    "destination": "Honor/Themes/串会话提交.hwt", "size": len(content),
                    "sha256": digest, "overwritten": False, "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection), self.assertRaisesRegex(PhoneTransferError, "会话标识不一致") as raised:
                upload_theme(path, device)

            self.assertEqual(raised.exception.code, "bad_response")
            self.assertEqual([request.method for request in ChunkedConnection.instances], ["PUT", "POST"])

    def test_chunked_upload_reports_remote_reset_when_legacy_fallback_also_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "重置连接主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            ChunkedConnection.instances = []
            # 4 次分块重试 + 整包 PUT 及它的一次幂等重试，全部断开。
            ChunkedConnection.plans = [OSError("[WinError 10054] 远程主机强迫关闭了一个现有的连接。")] * 6
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with (
                patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection),
                patch("hwtstudio.phone_transfer._remote_transfer_status", return_value=None) as status,
                patch("hwtstudio.phone_transfer._cancel_remote_transfer") as cancel,
                self.assertRaisesRegex(PhoneTransferError, "上传连接中断") as raised,
            ):
                upload_theme(path, device)

            self.assertEqual(raised.exception.code, "upload_interrupted")
            self.assertGreaterEqual(status.call_count, 3)
            cancel.assert_called_once()
            # 4 次分块重试耗尽后回退到整包 PUT，整包自身重试一次仍断开。
            self.assertEqual(len(ChunkedConnection.instances), 6)

    def test_chunked_upload_falls_back_to_legacy_put_when_chunk_connection_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "回退主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            completed = {
                "stored_name": "回退主题.hwt", "destination": "Honor/Themes/回退主题.hwt",
                "size": len(content), "sha256": digest, "overwritten": False,
                "theme_app_opened": False,
            }
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                OSError("Remote end closed connection without response"),
                OSError("Remote end closed connection without response"),
                OSError("Remote end closed connection without response"),
                OSError("Remote end closed connection without response"),
                (201, completed),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with (
                patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection),
                patch("hwtstudio.phone_transfer._remote_transfer_status", return_value=None),
                patch("hwtstudio.phone_transfer._cancel_remote_transfer") as cancel,
            ):
                result = upload_theme(path, device)

            self.assertEqual(result["sha256"], digest)
            cancel.assert_called_once()
            self.assertEqual(len(ChunkedConnection.instances), 5)
            legacy_request = ChunkedConnection.instances[-1]
            self.assertEqual(legacy_request.method, "PUT")
            self.assertIn("/api/v1/themes/", legacy_request.target)
            self.assertNotIn("X-HWT-Chunk-Offset", legacy_request.headers)

    def test_parallel_chunk_upload_sends_offsets_and_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "并行主题.hwt"
            content = b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            completed = {
                "stored_name": "并行主题.hwt", "destination": "Honor/Themes/并行主题.hwt",
                "size": len(content), "sha256": digest, "overwritten": False,
                "theme_app_opened": False,
            }
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (202, {
                    "state": "receiving", "transfer_id": "session", "received": CHUNK_SIZE,
                    "total": len(content), "next_offset": CHUNK_SIZE, "chunk_offset": 0,
                }),
                (202, {
                    "state": "receiving", "transfer_id": "session", "received": len(content),
                    "total": len(content), "next_offset": len(content), "chunk_offset": CHUNK_SIZE,
                }),
                (201, completed),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_PARALLEL],
            )
            stages = []
            with (
                patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection),
                patch("hwtstudio.phone_transfer.ThreadPoolExecutor", SerialExecutor),
            ):
                result = upload_theme(path, device, progress=lambda s, t, st: stages.append(st))

            self.assertEqual(result["sha256"], digest)
            self.assertEqual(len(ChunkedConnection.instances), 3)
            chunk_requests = [request for request in ChunkedConnection.instances if request.method == "PUT"]
            self.assertEqual(
                [request.headers["X-HWT-Chunk-Offset"] for request in chunk_requests],
                ["0", str(CHUNK_SIZE)],
            )
            self.assertEqual(bytes(chunk_requests[0].body), content[:CHUNK_SIZE])
            self.assertEqual(bytes(chunk_requests[1].body), content[CHUNK_SIZE:])
            self.assertTrue(ChunkedConnection.instances[-1].target.endswith("/complete"))
            parallel_stages = [stage for stage in stages if stage == "正在并行分块发送到手机"]
            self.assertEqual(len(parallel_stages), 2)

    def test_parallel_upload_falls_back_to_sequential_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "回退并行主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            completed = {
                "stored_name": path.name, "destination": f"Honor/Themes/{path.name}",
                "size": len(content), "sha256": digest, "overwritten": False,
                "theme_app_opened": False,
            }
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                OSError("并行块响应前断开"),
                OSError("并行块响应前断开"),
                OSError("并行块响应前断开"),
                OSError("并行块响应前断开"),
                (202, {
                    "state": "receiving", "transfer_id": "session", "received": len(content),
                    "total": len(content), "next_offset": len(content),
                }),
                (201, completed),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_PARALLEL, FEATURE_TRANSFER_CHUNKED],
            )
            stages = []
            with (
                patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection),
                patch("hwtstudio.phone_transfer.ThreadPoolExecutor", SerialExecutor),
                patch("hwtstudio.phone_transfer._cancel_remote_transfer") as cancel,
            ):
                result = upload_theme(path, device, progress=lambda s, t, st: stages.append(st))

            self.assertEqual(result["sha256"], digest)
            self.assertIn("并行发送失败，正在切换为顺序分块发送", stages)
            # 并行尝试 4 次各断开一次，之后顺序分块一块 + 提交一次。
            self.assertEqual(len(ChunkedConnection.instances), 6)
            self.assertEqual(cancel.call_count, 4)

    def test_parallel_upload_propagates_non_connection_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "并行校验失败主题.hwt"
            path.write_bytes(b"payload")
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (422, {"code": "hash_mismatch", "message": "分块 SHA-256 校验失败", "transfer_id": "session"}),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_PARALLEL],
            )

            with (
                patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection),
                patch("hwtstudio.phone_transfer.ThreadPoolExecutor", SerialExecutor),
                self.assertRaisesRegex(PhoneTransferError, "校验失败") as raised,
            ):
                upload_theme(path, device)

            self.assertEqual(raised.exception.code, "hash_mismatch")
            self.assertEqual(len(ChunkedConnection.instances), 1)

    def test_chunked_upload_does_not_resend_last_chunk_when_commit_response_is_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "提交主题.hwt"
            content = b"a" * CHUNK_SIZE + b"b" * CHUNK_SIZE
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            completed = {
                "state": "completed", "stored_name": "提交主题.hwt",
                "destination": "Honor/Themes/提交主题.hwt", "size": len(content),
                "sha256": digest, "overwritten": True, "theme_app_opened": False,
            }
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (202, {"state": "receiving", "transfer_id": "session", "received": CHUNK_SIZE,
                       "total": len(content), "next_offset": CHUNK_SIZE}),
                (202, {"state": "receiving", "transfer_id": "session", "received": len(content),
                       "total": len(content), "next_offset": len(content)}),
                OSError("提交响应前断开"),
                (202, {"state": "committing", "transfer_id": "session"}),
                (200, completed),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection):
                result = upload_theme(path, device)

            self.assertTrue(result["overwritten"])
            # 两个分块在同一连接上连续发送，提交失败后通过状态查询确认完成，
            # 没有重发最后一块。
            self.assertEqual(
                [request.method for request in ChunkedConnection.instances],
                ["PUT", "POST", "GET", "GET"],
            )
            upload_connection = ChunkedConnection.instances[0]
            self.assertEqual(bytes(upload_connection.body), content)
            self.assertEqual(sum(request.method == "PUT" for request in ChunkedConnection.instances), 1)

    def test_chunked_upload_refuses_file_changed_after_commit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "提交状态变化主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()

            class MutatingStatusConnection(ChunkedConnection):
                def getresponse(self):
                    # Peek at the next plan without consuming it.
                    next_plan = type(self).plans[0] if type(self).plans else None
                    if (
                        self.method == "GET"
                        and isinstance(next_plan, tuple)
                        and isinstance(next_plan[1], dict)
                        and next_plan[1].get("state") == "completed"
                    ):
                        path.write_bytes(b"changed after remote commit")
                    return super().getresponse()

            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (202, {
                    "state": "receiving", "transfer_id": "session", "received": len(content),
                    "total": len(content), "next_offset": len(content),
                }),
                OSError("提交响应前断开"),
                (200, {
                    "state": "completed", "stored_name": path.name,
                    "destination": f"Honor/Themes/{path.name}", "size": len(content),
                    "sha256": digest, "overwritten": False, "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with patch(
                "hwtstudio.phone_transfer.http.client.HTTPConnection",
                MutatingStatusConnection,
            ), self.assertRaisesRegex(PhoneTransferError, "状态确认后.*发生变化") as raised:
                upload_theme(path, device)

            self.assertEqual(raised.exception.code, "file_changed")

    def test_chunked_upload_refuses_file_changed_after_commit_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "提交响应变化主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()

            class MutatingCommitConnection(ChunkedConnection):
                def getresponse(self):
                    response = super().getresponse()
                    if self.method == "POST" and self.target.endswith("/complete"):
                        path.write_bytes(b"changed after commit response")
                    return response

            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (202, {
                    "state": "receiving", "transfer_id": "session", "received": len(content),
                    "total": len(content), "next_offset": len(content),
                }),
                (201, {
                    "stored_name": path.name, "destination": f"Honor/Themes/{path.name}",
                    "size": len(content), "sha256": digest, "overwritten": False,
                    "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with patch(
                "hwtstudio.phone_transfer.http.client.HTTPConnection",
                MutatingCommitConnection,
            ), self.assertRaisesRegex(PhoneTransferError, "提交响应后.*发生变化") as raised:
                upload_theme(path, device)

            self.assertEqual(raised.exception.code, "file_changed")

    def test_chunked_upload_cancels_before_commit_when_signal_arrives_after_last_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "提交前取消主题.hwt"
            path.write_bytes(b"payload")
            cancelled = threading.Event()

            class CancelAfterChunkConnection(ChunkedConnection):
                def getresponse(self):
                    response = super().getresponse()
                    if self.method == "PUT":
                        cancelled.set()
                    return response

            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (202, {
                    "state": "receiving", "transfer_id": "session", "received": 7,
                    "total": 7, "next_offset": 7,
                }),
                (202, {"code": "cancel_requested", "transfer_id": "session"}),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_CHUNKED],
            )

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", CancelAfterChunkConnection), self.assertRaisesRegex(TransferCancelled, "发送已取消"):
                upload_theme(path, device, cancelled=cancelled)

            self.assertEqual([request.method for request in ChunkedConnection.instances], ["PUT", "DELETE"])

    def test_legacy_upload_waits_for_commit_after_response_is_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "完整提交主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            completed = {
                "state": "completed", "stored_name": "完整提交主题.hwt",
                "destination": "Honor/Themes/完整提交主题.hwt", "size": len(content),
                "sha256": digest, "overwritten": False, "theme_app_opened": False,
            }
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                OSError("完整上传响应前断开"),
                (202, {"state": "committing", "transfer_id": "session"}),
                (200, completed),
            ]
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection):
                result = upload_theme(path, device)

            self.assertEqual(result["sha256"], digest)
            self.assertEqual([request.method for request in ChunkedConnection.instances], ["PUT", "GET", "GET"])

    def test_legacy_upload_waits_for_receiving_session_before_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "等待释放主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                OSError("完整上传响应前断开"),
                (202, {"state": "receiving", "transfer_id": "session"}),
                (404, {"state": "not_found", "transfer_id": "session"}),
                (201, {
                    "stored_name": "等待释放主题.hwt",
                    "destination": "Honor/Themes/等待释放主题.hwt",
                    "size": len(content), "sha256": digest,
                    "overwritten": False, "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection):
                result = upload_theme(path, device)

            self.assertEqual(result["sha256"], digest)
            self.assertEqual(
                [request.method for request in ChunkedConnection.instances],
                ["PUT", "GET", "GET", "PUT"],
            )

    def test_legacy_retry_does_not_repeat_metadata_prepare_for_active_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "预检重试主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            completed = {
                "stored_name": path.name, "destination": f"Honor/Themes/{path.name}",
                "size": len(content), "sha256": digest, "overwritten": False,
                "theme_app_opened": False,
            }
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [OSError("预检后的完整上传响应前断开"), (201, completed)]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_PREPARE],
            )

            with (
                patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection),
                patch("hwtstudio.phone_transfer._prepare_transfer", return_value=True) as prepare,
                patch(
                    "hwtstudio.phone_transfer._remote_transfer_status",
                    side_effect=[{"state": "receiving"}] * 40,
                ),
                patch("hwtstudio.phone_transfer.time.sleep"),
            ):
                result = upload_theme(path, device)

            self.assertEqual(result["sha256"], digest)
            prepare.assert_called_once()
            self.assertEqual([request.method for request in ChunkedConnection.instances], ["PUT", "PUT"])

    def test_metadata_prepare_is_verified_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "预检主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            transfer_id = "a" * 32
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (200, {
                    "state": "prepared", "transfer_id": transfer_id, "file_name": "预检主题.hwt",
                    "size": len(content), "sha256": digest,
                }),
                (201, {
                    "stored_name": "预检主题.hwt", "destination": "Honor/Themes/预检主题.hwt",
                    "size": len(content), "sha256": digest, "overwritten": False,
                    "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_PREPARE],
            )

            with (
                patch("hwtstudio.phone_transfer.uuid.uuid4", return_value=type("TransferId", (), {"hex": transfer_id})()),
                patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection),
            ):
                upload_theme(path, device)

            prepare, upload = ChunkedConnection.instances
            self.assertEqual([prepare.method, upload.method], ["POST", "PUT"])
            self.assertTrue(prepare.target.endswith("/prepare"))
            metadata = json.loads(bytes(prepare.body).decode("utf-8"))
            self.assertEqual(metadata, {"file_name": "预检主题.hwt", "size": len(content), "sha256": digest})

    def test_metadata_prepare_404_falls_back_to_legacy_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "旧助手主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (404, {}),
                (201, {
                    "stored_name": "旧助手主题.hwt", "destination": "Honor/Themes/旧助手主题.hwt",
                    "size": len(content), "sha256": digest, "overwritten": False,
                    "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_PREPARE],
            )

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection):
                result = upload_theme(path, device)

            self.assertEqual(result["sha256"], digest)
            self.assertEqual([request.method for request in ChunkedConnection.instances], ["POST", "PUT"])

    def test_metadata_prepare_precedes_chunked_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "预检分块主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            transfer_id = "b" * 32
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (200, {
                    "state": "prepared", "transfer_id": transfer_id, "file_name": "预检分块主题.hwt",
                    "size": len(content), "sha256": digest,
                }),
                (202, {
                    "state": "receiving", "transfer_id": transfer_id, "received": len(content),
                    "total": len(content), "next_offset": len(content),
                }),
                (201, {
                    "stored_name": "预检分块主题.hwt", "destination": "Honor/Themes/预检分块主题.hwt",
                    "size": len(content), "sha256": digest, "overwritten": False,
                    "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice(
                "phone-1", "测试手机", "127.0.0.1", token="token",
                features=[FEATURE_TRANSFER_PREPARE, FEATURE_TRANSFER_CHUNKED],
            )

            with (
                patch("hwtstudio.phone_transfer.uuid.uuid4", return_value=type("TransferId", (), {"hex": transfer_id})()),
                patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection),
            ):
                upload_theme(path, device)

            self.assertEqual([request.method for request in ChunkedConnection.instances], ["POST", "PUT", "POST"])
            self.assertTrue(ChunkedConnection.instances[1].target.endswith(transfer_id))

    def test_upload_without_chunk_feature_keeps_legacy_put(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "旧版主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (201, {
                    "stored_name": "旧版主题.hwt", "destination": "Honor/Themes/旧版主题.hwt",
                    "size": len(content), "sha256": digest, "overwritten": False,
                    "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection):
                upload_theme(path, device)

            request = ChunkedConnection.instances[0]
            self.assertEqual(request.method, "PUT")
            self.assertIn("/api/v1/themes/", request.target)
            self.assertNotIn("X-HWT-Chunk-Offset", request.headers)

    def test_legacy_upload_rejects_mismatched_response_transfer_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "完整会话绑定主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (201, {
                    "transfer_id": "another-session", "stored_name": "完整会话绑定主题.hwt",
                    "destination": "Honor/Themes/完整会话绑定主题.hwt", "size": len(content),
                    "sha256": digest, "overwritten": False, "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection), self.assertRaisesRegex(PhoneTransferError, "会话标识不一致") as raised:
                upload_theme(path, device)

            self.assertEqual(raised.exception.code, "bad_response")
            self.assertEqual([request.method for request in ChunkedConnection.instances], ["PUT"])

    def test_upload_refuses_file_changed_after_hashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.hwt"
            path.write_bytes(b"original")
            before = path.stat()

            def hash_then_mutate(_path, *, cancelled=None, progress=None):
                path.write_bytes(b"changed")
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
                return "0" * 64

            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")
            with patch("hwtstudio.phone_transfer.sha256_file", side_effect=hash_then_mutate), self.assertRaisesRegex(PhoneTransferError, "发生变化") as raised:
                upload_theme(path, device)
            self.assertEqual(raised.exception.code, "file_changed")

    def test_upload_refuses_file_changed_during_send(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.hwt"
            path.write_bytes(b"original")
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")

            def mutate(_data):
                path.write_bytes(b"changed")

            with (
                patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection),
                patch.object(FakeHttpConnection, "send", side_effect=mutate),
                self.assertRaisesRegex(PhoneTransferError, "发送后") as raised,
            ):
                upload_theme(path, device)
            self.assertEqual(raised.exception.code, "file_changed")

    def test_legacy_upload_refuses_file_changed_after_response(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "响应后变化主题.hwt"
            content = b"payload"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()

            class MutatingResponseConnection(ChunkedConnection):
                def getresponse(self):
                    response = super().getresponse()
                    if self.method == "PUT":
                        path.write_bytes(b"changed after response")
                    return response

            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                (201, {
                    "stored_name": path.name, "destination": f"Honor/Themes/{path.name}",
                    "size": len(content), "sha256": digest, "overwritten": False,
                    "theme_app_opened": False,
                }),
            ]
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")

            with patch(
                "hwtstudio.phone_transfer.http.client.HTTPConnection",
                MutatingResponseConnection,
            ), self.assertRaisesRegex(PhoneTransferError, "响应确认后.*发生变化") as raised:
                upload_theme(path, device)

            self.assertEqual(raised.exception.code, "file_changed")

    def test_malformed_remote_protocol_is_reported_as_bad_response(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token", features=["device_profile"])
        FakeHttpConnection.response_payload = {"protocol": "not-a-number"}
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "协议版本") as raised:
            probe_phone(device.host, device.port)
        self.assertEqual(raised.exception.code, "bad_response")

    def test_remote_text_fields_are_bounded_and_errors_are_single_line(self):
        FakeHttpConnection.response_payload = {
            "protocol": 1,
            "device_id": "phone-1",
            "name": "x" * 513,
        }
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "过长") as raised:
            probe_phone("127.0.0.1")
        self.assertEqual(raised.exception.code, "bad_response")

        error = _error_from_response(500, {"message": "x" * 600 + "\n第二行\x00", "code": "remote"})
        self.assertNotIn("\n", str(error))
        self.assertNotIn("\x00", str(error))
        self.assertLessEqual(len(str(error)), MAX_REMOTE_ERROR_CHARS + 3)

    def test_remote_text_fields_reject_control_characters_at_edges(self):
        for value in ("\nphone-1", "phone-1\n", "\tphone-1", "phone-1\t"):
            FakeHttpConnection.response_payload = {
                "protocol": 1,
                "device_id": value,
                "name": "测试手机",
            }
            with self.subTest(value=repr(value)), patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "过长或包含控制字符") as raised:
                probe_phone("127.0.0.1")
            self.assertEqual(raised.exception.code, "bad_response")

    def test_oversized_remote_response_is_rejected(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1")
        FakeHttpConnection.response_payload = {
            "protocol": 1,
            "device_id": "phone-1",
            "padding": "x" * MAX_RESPONSE_BYTES,
        }
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "过大") as raised:
            probe_phone(device.host, device.port)
        self.assertEqual(raised.exception.code, "bad_response")

    def test_invalid_remote_content_length_is_rejected(self):
        FakeHttpConnection.response_payload = {"protocol": 1}
        FakeHttpConnection.response_headers = {"Content-Length": "not-a-number"}
        try:
            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "响应长度") as raised:
                probe_phone("127.0.0.1")
            self.assertEqual(raised.exception.code, "bad_response")
        finally:
            FakeHttpConnection.response_headers = {}

    def test_truncated_remote_response_is_rejected(self):
        FakeHttpConnection.response_payload = {"protocol": 1}
        body_length = len(json.dumps(FakeHttpConnection.response_payload).encode("utf-8"))
        FakeHttpConnection.response_headers = {"Content-Length": str(body_length + 1)}
        try:
            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "长度与声明不一致") as raised:
                probe_phone("127.0.0.1")
            self.assertEqual(raised.exception.code, "bad_response")
        finally:
            FakeHttpConnection.response_headers = {}

    def test_missing_remote_device_id_is_reported_as_bad_response(self):
        FakeHttpConnection.response_payload = {"protocol": 1}
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "device_id") as raised:
            probe_phone("127.0.0.1")
        self.assertEqual(raised.exception.code, "bad_response")

    def test_malformed_pair_token_is_reported_as_bad_response(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1")
        FakeHttpConnection.response_payload = {"protocol": 1, "token": {}}
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "token") as raised:
            pair_phone(device, "123456")
        self.assertEqual(raised.exception.code, "bad_response")

    def test_pair_rejects_unbounded_remote_device_id(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1")
        FakeHttpConnection.response_payload = {
            "protocol": 1,
            "token": "token",
            "device_id": "x" * (MAX_REMOTE_TEXT_CHARS + 1),
        }
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "配对响应device_id.*过长") as raised:
            pair_phone(device, "123456")
        self.assertEqual(raised.exception.code, "bad_response")

    def test_pair_compacts_remote_error_message(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1")
        FakeHttpConnection.response_status = 400
        FakeHttpConnection.response_payload = {"message": "x" * 600 + "\n第二行\x00"}
        try:
            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaises(PhoneTransferError) as raised:
                pair_phone(device, "123456")
            self.assertEqual(raised.exception.code, "pair_failed")
            self.assertNotIn("\n", str(raised.exception))
            self.assertNotIn("\x00", str(raised.exception))
            self.assertLessEqual(len(str(raised.exception)), MAX_REMOTE_ERROR_CHARS + 3)
        finally:
            FakeHttpConnection.response_status = 200

    def test_upload_rejects_mismatched_remote_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.hwt"
            path.write_bytes(b"payload")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            FakeHttpConnection.response_payload = {"size": 999, "sha256": digest}
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")
            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "大小") as raised:
                upload_theme(path, device)
            self.assertEqual(raised.exception.code, "bad_response")

    def test_upload_rejects_non_boolean_success_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.hwt"
            path.write_bytes(b"payload")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            FakeHttpConnection.response_payload = {
                "size": path.stat().st_size,
                "sha256": digest,
                "overwritten": "false",
            }
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")
            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "overwritten") as raised:
                upload_theme(path, device)
            self.assertEqual(raised.exception.code, "bad_response")

    def test_upload_rejects_missing_success_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.hwt"
            path.write_bytes(b"payload")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")
            for missing in ("overwritten", "theme_app_opened"):
                FakeHttpConnection.response_payload = {
                    "size": path.stat().st_size,
                    "sha256": digest,
                    "overwritten": False,
                    "theme_app_opened": False,
                }
                FakeHttpConnection.response_payload.pop(missing)
                with self.subTest(missing=missing), patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, missing) as raised:
                    upload_theme(path, device)
                self.assertEqual(raised.exception.code, "bad_response")

    def test_malformed_profile_fields_are_rejected_without_partial_state(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token", features=["device_profile"])
        FakeHttpConnection.response_payload = {"sdk_int": "not-a-number", "installed_packages": "bad"}
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, "SDK") as raised:
            fetch_phone_profile(device)
        self.assertEqual(raised.exception.code, "bad_response")
        self.assertIsNone(device.profile)

    def test_profile_rejects_boolean_sdk_and_non_string_package(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token", features=["device_profile"])
        for payload, message in (
            ({"sdk_int": True}, "SDK"),
            ({"sdk_int": 36, "model": 7}, "model"),
        ):
            FakeHttpConnection.response_payload = payload
            with self.subTest(message=message), patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection), self.assertRaisesRegex(PhoneTransferError, message) as raised:
                fetch_phone_profile(device)
            self.assertEqual(raised.exception.code, "bad_response")
            self.assertIsNone(device.profile)

    def test_registry_discards_malformed_entries_and_normalizes_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            path.write_text(json.dumps({
                    "devices": [
                        "not a device",
                        {"device_id": "missing-host"},
                        {"device_id": "bad-port", "host": "127.0.0.1", "port": 70000},
                        {"device_id": "boolean-port", "host": "127.0.0.1", "port": True},
                        {"device_id": True, "host": "127.0.0.1"},
                        {"device_id": "bad-host-type", "host": 123},
                        {"device_id": "bad-token-type", "host": "127.0.0.1", "token": {}},
                        {"device_id": "boolean-protocol", "host": "127.0.0.1", "protocol": False},
                        {"device_id": "future-protocol", "host": "127.0.0.1", "protocol": 99},
                        {
                        "device_id": "valid",
                        "host": "127.0.0.1",
                        "profile": {
                            "model": "测试机",
                            "sdk_int": -36,
                            "installed_packages": [" com.example ", 7],
                        },
                    },
                ],
            }), encoding="utf-8")
            devices = PhoneRegistry(path).load()
            self.assertEqual(set(devices), {"valid"})
            self.assertEqual(devices["valid"].profile.model, "测试机")
            self.assertEqual(devices["valid"].profile.sdk_int, 0)
            self.assertEqual(devices["valid"].profile.installed_packages, ["com.example"])

    def test_registry_rejects_string_and_float_numeric_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            path.write_text(json.dumps({
                "devices": [
                    {"device_id": "string-port", "host": "127.0.0.1", "port": "48621"},
                    {"device_id": "float-protocol", "host": "127.0.0.1", "protocol": 1.0},
                    {
                        "device_id": "string-sdk",
                        "host": "127.0.0.1",
                        "profile": {"sdk_int": "36"},
                    },
                ],
            }), encoding="utf-8")
            devices = PhoneRegistry(path).load()
            self.assertEqual(set(devices), {"string-sdk"})
            self.assertEqual(devices["string-sdk"].profile.sdk_int, 0)

    def test_registry_bounds_cached_text_and_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            path.write_text(json.dumps({
                "devices": [
                    {
                        "device_id": "valid",
                        "host": "127.0.0.1",
                        "name": "n" * 600,
                        "token": "t" * 600,
                        "profile": {"installed_packages": ["p" * 600, "com.example"]},
                    },
                    {"device_id": "i" * 600, "host": "127.0.0.1"},
                ],
            }), encoding="utf-8")
            devices = PhoneRegistry(path).load()
            self.assertEqual(set(devices), {"valid"})
            self.assertEqual(devices["valid"].name, "n" * 512)
            self.assertEqual(devices["valid"].token, "")
            self.assertEqual(devices["valid"].profile.installed_packages, ["com.example"])

    def test_registry_returns_empty_for_invalid_top_level_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(PhoneRegistry(path).load(), {})

    def test_registry_returns_empty_for_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            path.write_bytes(b"\xff\xfe")
            self.assertEqual(PhoneRegistry(path).load(), {})

    def test_registry_rejects_oversized_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            path.write_bytes(b"x" * (MAX_REGISTRY_BYTES + 1))
            self.assertEqual(PhoneRegistry(path).load(), {})

    def test_registry_save_rejects_oversized_payload_before_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            device = PhoneDevice("phone-1", "x" * (MAX_REGISTRY_BYTES + 1), "127.0.0.1")
            with self.assertRaisesRegex(ValueError, "超过允许的大小"):
                PhoneRegistry(path).save({device.device_id: device})
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_registry_preserves_corrupt_file_instead_of_silent_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            path.write_bytes(b'{"devices": [ broken')
            self.assertEqual(PhoneRegistry(path).load(), {})
            backups = list(Path(directory).glob("phones.json.corrupt-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b'{"devices": [ broken')

    def test_registry_save_survives_failed_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="tok-123")
            registry = PhoneRegistry(path)
            with patch("hwtstudio.phone_transfer.os.replace", side_effect=OSError("模拟不支持原子替换")):
                registry.save({device.device_id: device})
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])
            loaded = registry.load()
            self.assertEqual(loaded["phone-1"].name, "测试手机")
            self.assertEqual(loaded["phone-1"].token, "tok-123")

    def test_registry_roundtrips_pair_code_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", pair_code="654321")
            PhoneRegistry(path).save({device.device_id: device})
            loaded = PhoneRegistry(path).load()
            self.assertEqual(loaded["phone-1"].pair_code, "654321")

    def test_registry_loads_legacy_file_without_pair_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            path.write_text(json.dumps({
                "devices": [{"device_id": "old", "host": "127.0.0.1", "name": "旧记录"}],
            }), encoding="utf-8")
            loaded = PhoneRegistry(path).load()
            self.assertEqual(loaded["old"].pair_code, "")

    def test_fetch_phone_profile_tolerates_dirty_package_list(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="tok-1", features=["device_profile"])
        FakeHttpConnection.response_payload = {
            "manufacturer": "HONOR",
            "model": "PAD",
            "sdk_int": 34,
            "installed_packages": ["com.example.good", 123, None, ""],
        }
        with tempfile.TemporaryDirectory() as directory:
            registry = PhoneRegistry(Path(directory) / "phones.json")
            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
                profile = fetch_phone_profile(device, registry=registry)
            self.assertEqual(profile.installed_packages, ["com.example.good"])

    def test_registry_rejects_symlinked_parent_before_lock_creation(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            link = root / "storage"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建目录符号链接：{exc}")
            with self.assertRaisesRegex(OSError, "手机记录文件目录.*符号链接"):
                PhoneRegistry(link / "phones.json").save({})
            self.assertEqual(list(outside.iterdir()), [])

    def test_registry_rejects_symlinked_target_and_lock_file(self):
        if not hasattr(os, "symlink"):
            self.skipTest("当前平台不支持符号链接")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.json"
            outside.write_text("keep", encoding="utf-8")
            target = root / "phones.json"
            try:
                os.symlink(outside, target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建文件符号链接：{exc}")
            with self.assertRaisesRegex(OSError, "手机记录文件.*普通文件"):
                PhoneRegistry(target).save({})
            self.assertTrue(target.is_symlink())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

            target.unlink()
            lock_target = root / "outside.lock"
            lock_target.write_bytes(b"keep")
            lock_path = root / ".phones.json.lock"
            try:
                os.symlink(lock_target, lock_path)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"当前环境无法创建锁文件符号链接：{exc}")
            with self.assertRaisesRegex(OSError, "锁文件.*符号链接"):
                PhoneRegistry(target).save({})
            self.assertEqual(lock_target.read_bytes(), b"keep")

    def test_registry_load_fails_closed_when_lock_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            with patch("hwtstudio.phone_transfer._interprocess_lock", side_effect=OSError("locked")):
                self.assertEqual(PhoneRegistry(path).load(), {})

    def test_registry_lock_has_bounded_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            lock_call = "hwtstudio.locking.msvcrt.locking" if os.name == "nt" else "hwtstudio.locking.fcntl.flock"
            with (
                patch("hwtstudio.locking.time.monotonic", side_effect=[0.0, 6.0]),
                patch(lock_call, side_effect=OSError("busy")),
                self.assertRaisesRegex(OSError, "超时"),_interprocess_lock(path)
            ):
                pass

    def test_registry_serializes_concurrent_updates_from_separate_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            count = 12
            barrier = threading.Barrier(count)

            def update(index: int):
                barrier.wait()
                PhoneRegistry(path).update(
                    PhoneDevice(f"phone-{index}", f"测试手机 {index}", f"10.0.0.{index + 1}")
                )

            with ThreadPoolExecutor(max_workers=count) as executor:
                list(executor.map(update, range(count)))

            self.assertEqual(set(PhoneRegistry(path).load()), {f"phone-{index}" for index in range(count)})

    def test_http_discovery_fallback_uses_only_valid_private_targets(self):
        HttpDiscoveryConnection.calls = []
        with tempfile.TemporaryDirectory() as directory:
            registry = PhoneRegistry(Path(directory) / "phones.json")
            with (
                patch("hwtstudio.phone_transfer.socket.socket", SilentDiscoverySocket),
                patch("hwtstudio.phone_transfer.http.client.HTTPConnection", HttpDiscoveryConnection),
            ):
                devices = discover_phones(
                    timeout=1.0,
                    registry=registry,
                    http_targets=["10.0.0.9", "10.0.0.8", "not-an-ip", "8.8.8.8"],
                )

        self.assertEqual([device.device_id for device in devices], ["http-phone-1"])
        self.assertEqual(devices[0].host, "10.0.0.8")
        self.assertEqual({call[0] for call in HttpDiscoveryConnection.calls}, {"10.0.0.8", "10.0.0.9"})

    def test_http_discovery_is_skipped_when_udp_already_found_a_phone(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = PhoneRegistry(Path(directory) / "phones.json")
            with (
                patch("hwtstudio.phone_transfer.socket.socket", FakeDiscoverySocket),
                patch("hwtstudio.phone_transfer.http.client.HTTPConnection", side_effect=AssertionError("unexpected scan")),
            ):
                devices = discover_phones(
                    timeout=0.01,
                    registry=registry,
                    http_targets=["10.0.0.8"],
                )

        self.assertEqual([device.device_id for device in devices], ["phone-1"])

    def test_http_discovery_discards_malformed_status(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = PhoneRegistry(Path(directory) / "phones.json")
            with (
                patch("hwtstudio.phone_transfer.socket.socket", SilentDiscoverySocket),
                patch(
                    "hwtstudio.phone_transfer.http.client.HTTPConnection",
                    InvalidHttpDiscoveryConnection,
                ),
            ):
                devices = discover_phones(
                    timeout=0.2,
                    registry=registry,
                    http_targets=["10.0.0.8"],
                )

        self.assertEqual(devices, [])
        self.assertEqual(registry.load(), {})

    def test_bounded_ipv4_discovery_targets_skip_large_and_public_networks(self):
        self.assertEqual(
            bounded_ipv4_discovery_targets(
                [("192.168.10.20", "255.255.255.0")], limit=3,
            ),
            ["192.168.10.1", "192.168.10.2", "192.168.10.3"],
        )
        self.assertEqual(
            bounded_ipv4_discovery_targets(
                [("10.0.0.20", "255.255.0.0"), ("8.8.8.8", "255.255.255.0")],
            ),
            [],
        )

    def test_http_discovery_candidates_prioritize_saved_devices(self):
        saved = {
            "phone-1": PhoneDevice("phone-1", "上次连接", "192.168.0.154", port=48621),
        }
        targets = ["192.168.0.1", "192.168.0.154", "192.168.0.2", "8.8.8.8"]

        candidates = _http_discovery_candidates(saved, targets)

        self.assertEqual(candidates[0], "192.168.0.154")
        self.assertEqual(set(candidates), {"192.168.0.154", "192.168.0.1", "192.168.0.2"})
        self.assertEqual(len(candidates), 3)
        # 没有网卡子网可扫时，也至少直接探测上次连接过的地址。
        self.assertEqual(_http_discovery_candidates(saved, None), ["192.168.0.154"])

    def test_http_discovery_candidates_drop_invalid_and_non_private_targets(self):
        saved = {}
        candidates = _http_discovery_candidates(saved, ["not-an-ip", "8.8.8.8", "10.0.0.8"])
        self.assertEqual(candidates, ["10.0.0.8"])

    def test_discovery_discards_invalid_remote_port(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = PhoneRegistry(Path(directory) / "phones.json")
            with patch("hwtstudio.phone_transfer.socket.socket", InvalidPortDiscoverySocket):
                devices = discover_phones(timeout=0.01, registry=registry)
            self.assertEqual(devices, [])
            self.assertEqual(registry.load(), {})

    def test_discovery_discards_missing_device_id(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = PhoneRegistry(Path(directory) / "phones.json")
            with patch("hwtstudio.phone_transfer.socket.socket", MissingDeviceIdDiscoverySocket):
                devices = discover_phones(timeout=0.01, registry=registry)
            self.assertEqual(devices, [])

    def test_discovery_discards_non_object_json(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = PhoneRegistry(Path(directory) / "phones.json")
            with patch("hwtstudio.phone_transfer.socket.socket", InvalidShapeDiscoverySocket):
                devices = discover_phones(timeout=0.01, registry=registry)
            self.assertEqual(devices, [])

    def test_registry_and_discovery_preserve_token(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = PhoneRegistry(Path(directory) / "phones.json")
            registry.update(PhoneDevice("phone-1", "旧名称", "10.0.0.8", token="secret"))
            with patch("hwtstudio.phone_transfer.socket.socket", FakeDiscoverySocket):
                devices = discover_phones(timeout=0.01, registry=registry)
            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0].host, "10.0.0.8")
            self.assertEqual(devices[0].token, "secret")
            self.assertEqual(registry.load()["phone-1"].host, "10.0.0.8")

    def test_discovery_does_not_reuse_stale_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = PhoneRegistry(Path(directory) / "phones.json")
            registry.update(PhoneDevice(
                "phone-1", "旧名称", "10.0.0.2", features=[FEATURE_TRANSFER_CHUNKED],
            ))
            with patch("hwtstudio.phone_transfer.socket.socket", FakeDiscoverySocket):
                devices = discover_phones(timeout=0.01, registry=registry)

            self.assertEqual(devices[0].features, ["device_profile"])
            self.assertNotIn(FEATURE_TRANSFER_CHUNKED, registry.load()["phone-1"].features)

    def test_probe_pair_and_stream_upload(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ReceiverHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                registry = PhoneRegistry(root / "phones.json")
                theme = root / "中文 主题.hwt"
                with zipfile.ZipFile(theme, "w") as archive:
                    archive.writestr("description.xml", "<HwTheme/>")
                    archive.writestr("wallpaper/home_wallpaper_0.jpg", b"image")
                device = probe_phone("127.0.0.1", server.server_port, registry=registry)
                paired = pair_phone(device, "123456", registry=registry)
                profile = fetch_phone_profile(paired, registry=registry)
                self.assertEqual(profile.model, "ELP-AN00")
                self.assertEqual(profile.installed_packages, ["com.android.settings", "com.tencent.mm"])
                self.assertEqual(registry.load()["phone-1"].profile.os_name, "MagicOS_10.0.0")
                progress = []
                result = upload_theme(theme, paired, progress=lambda sent, total, stage: progress.append((sent, total, stage)))
                self.assertEqual(ReceiverHandler.received, theme.read_bytes())
                self.assertEqual(result["sha256"], hashlib.sha256(theme.read_bytes()).hexdigest())
                self.assertTrue(progress)
                hash_progress = [
                    (completed, total)
                    for completed, total, stage in progress
                    if stage == "正在校验文件完整性（SHA-256）"
                ]
                self.assertEqual(hash_progress[0], (0, theme.stat().st_size))
                self.assertEqual(hash_progress[-1], (theme.stat().st_size, theme.stat().st_size))
                self.assertIn((0, 0, "正在准备发送到手机"), progress)
                self.assertEqual(registry.load()["phone-1"].token, "test-token")
                manual = PhoneDevice("manual:127.0.0.1", "手动", "127.0.0.1", server.server_port)
                reused = transfer_to_app(theme, manual, registry=registry)
            self.assertEqual(reused["transport"], "apk")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_cancelled_upload_requests_remote_cancellation_when_supported(self):
        class RecordingConnection(FakeHttpConnection):
            def __init__(self, *_args, **_kwargs):
                self.headers = {}
                self.requests = []

            def putheader(self, name, value):
                self.headers[name] = value

            def request(self, *args, **kwargs):
                self.requests.append((args, kwargs))

            def getresponse(self):
                return FakeHttpResponse({}, status=202)

        with tempfile.TemporaryDirectory() as directory:
            theme = Path(directory) / "cancel.hwt"
            theme.write_bytes(b"x" * (CHUNK_SIZE * 2))
            upload_connection = RecordingConnection()
            cancel_connection = RecordingConnection()
            cancelled = threading.Event()

            def progress(sent, _total, stage):
                if sent and stage == "正在发送到手机":
                    cancelled.set()

            with patch(
                "hwtstudio.phone_transfer.http.client.HTTPConnection",
                side_effect=[upload_connection, cancel_connection],
            ), self.assertRaises(TransferCancelled):
                upload_theme(
                    theme,
                    PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="test-token"),
                    cancelled=cancelled,
                    progress=progress,
                )

            self.assertRegex(upload_connection.headers["X-HWT-Transfer-Id"], r"^[0-9a-f]{32}$")
            self.assertEqual(cancel_connection.requests[0][0][0], "DELETE")
            self.assertIn(upload_connection.headers["X-HWT-Transfer-Id"], cancel_connection.requests[0][0][1])

    def test_connection_failure_retries_once_with_same_transfer_id(self):
        class RetryConnection(FakeHttpConnection):
            def __init__(self, fail=False, status=201, state=None, *_args, **_kwargs):
                self.fail = fail
                self.status = status
                self.state = state
                self.headers = {}

            def putheader(self, name, value):
                self.headers[name] = value

            def getresponse(self):
                if self.fail:
                    raise OSError("连接在响应前断开")
                if self.status == 404:
                    return FakeHttpResponse({}, status=404)
                payload = b"payload"
                body = {
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "destination": "Honor/Themes/retried.hwt",
                    "overwritten": False,
                    "theme_app_opened": False,
                }
                if self.state:
                    body["state"] = self.state
                return FakeHttpResponse(body, status=self.status)

        with tempfile.TemporaryDirectory() as directory:
            theme = Path(directory) / "retried.hwt"
            theme.write_bytes(b"payload")
            first = RetryConnection(fail=True)
            status = RetryConnection(status=404)
            second = RetryConnection()
            stages = []
            with patch(
                "hwtstudio.phone_transfer.http.client.HTTPConnection",
                side_effect=[first, status, second],
            ):
                result = upload_theme(
                    theme,
                    PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="test-token"),
                    progress=lambda _sent, _total, stage: stages.append(stage),
                )

            self.assertEqual(result["remote"], "Honor/Themes/retried.hwt")
            self.assertEqual(first.headers["X-HWT-Transfer-Id"], second.headers["X-HWT-Transfer-Id"])
            self.assertIn("网络中断，正在重试上传", stages)

    def test_completed_transfer_status_avoids_resending_file(self):
        class StatusConnection(FakeHttpConnection):
            def __init__(self, fail=False, *_args, **_kwargs):
                self.fail = fail
                self.headers = {}

            def putheader(self, name, value):
                self.headers[name] = value

            def getresponse(self):
                if self.fail:
                    raise OSError("连接在响应前断开")
                payload = b"payload"
                return FakeHttpResponse(
                    {
                        "state": "completed",
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "destination": "Honor/Themes/already-done.hwt",
                        "overwritten": True,
                        "theme_app_opened": False,
                    },
                    status=200,
                )

        with tempfile.TemporaryDirectory() as directory:
            theme = Path(directory) / "already-done.hwt"
            theme.write_bytes(b"payload")
            first = StatusConnection(fail=True)
            status = StatusConnection()
            stages = []
            with patch(
                "hwtstudio.phone_transfer.http.client.HTTPConnection",
                side_effect=[first, status],
            ):
                result = upload_theme(
                    theme,
                    PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="test-token"),
                    progress=lambda _sent, _total, stage: stages.append(stage),
                )

            self.assertEqual(result["remote"], "Honor/Themes/already-done.hwt")
            self.assertTrue(result["overwritten"])
            self.assertIn("手机已完成上传，正在确认结果", stages)

    def test_completed_transfer_status_rejects_file_changed_after_initial_upload(self):
        class MutatingStatusConnection(FakeHttpConnection):
            def __init__(self, fail=False, *_args, **_kwargs):
                self.fail = fail
                self.headers = {}

            def putheader(self, name, value):
                self.headers[name] = value

            def getresponse(self):
                if self.fail:
                    raise OSError("连接在响应前断开")
                theme.write_bytes(b"changed")
                os.utime(theme, ns=(theme.stat().st_atime_ns, initial_mtime_ns))
                payload = b"payload"
                return FakeHttpResponse(
                    {
                        "state": "completed",
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "destination": "Honor/Themes/already-done.hwt",
                        "overwritten": True,
                        "theme_app_opened": False,
                    },
                    status=200,
                )

        with tempfile.TemporaryDirectory() as directory:
            theme = Path(directory) / "already-done-changed.hwt"
            theme.write_bytes(b"payload")
            initial_mtime_ns = theme.stat().st_mtime_ns
            with patch(
                "hwtstudio.phone_transfer.http.client.HTTPConnection",
                side_effect=[MutatingStatusConnection(fail=True), MutatingStatusConnection()],
            ), self.assertRaisesRegex(PhoneTransferError, "状态确认后.*发生变化") as raised:
                upload_theme(
                    theme,
                    PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="test-token"),
                )

            self.assertEqual(raised.exception.code, "file_changed")

    def test_unknown_remote_transfer_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            theme = Path(directory) / "未知状态.hwt"
            theme.write_bytes(b"payload")
            ChunkedConnection.instances = []
            ChunkedConnection.plans = [
                OSError("上传响应前断开"),
                (200, {"state": "unknown"}),
            ]
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection), self.assertRaisesRegex(PhoneTransferError, "未知的传输状态") as raised:
                upload_theme(theme, device)

            self.assertEqual(raised.exception.code, "bad_response")
            self.assertEqual([request.method for request in ChunkedConnection.instances], ["PUT", "GET"])

    def test_safe_filename(self):
        self.assertEqual(safe_hwt_filename("../我的 主题.hwt"), "我的_主题.hwt")
        self.assertEqual(safe_hwt_filename("theme"), "theme.hwt")

    def test_safe_filename_is_bounded_in_utf8_bytes(self):
        filename = safe_hwt_filename("主题" * 200)
        self.assertLessEqual(len(filename.encode("utf-8")), MAX_FILENAME_BYTES)
        self.assertTrue(filename.endswith(".hwt"))


if __name__ == "__main__":
    unittest.main()
