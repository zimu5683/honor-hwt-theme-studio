from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from hwtstudio.phone_transfer import (
    CHUNK_SIZE,
    DISCOVERY_REQUEST,
    FEATURE_TRANSFER_CHUNKED,
    FEATURE_TRANSFER_PREPARE,
    MAX_FILENAME_BYTES,
    MAX_REMOTE_ERROR_CHARS,
    MAX_REGISTRY_BYTES,
    MAX_RESPONSE_BYTES,
    PhoneDevice,
    PhoneRegistry,
    PhoneTransferError,
    TransferCancelled,
    _error_from_response,
    _interprocess_lock,
    discover_phones,
    fetch_phone_profile,
    pair_phone,
    probe_phone,
    safe_hwt_filename,
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
    response_payload = {}
    response_status = 200
    response_headers = {}

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
    plans = []
    instances = []

    def __init__(self, *_args, **_kwargs):
        self.headers = {}
        self.method = None
        self.target = None
        self.body = bytearray()
        self.plan = type(self).plans.pop(0)
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
        if isinstance(self.plan, BaseException):
            raise self.plan
        status, payload = self.plan
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
        raise socket.timeout()

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
        raise socket.timeout()


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
        raise socket.timeout()


class InvalidShapeDiscoverySocket(FakeDiscoverySocket):
    def recvfrom(self, _size):
        if self.sent and not self.returned:
            self.returned = True
            return b"[]", ("10.0.0.11", 48620)
        raise socket.timeout()


class PhoneTransferTests(unittest.TestCase):
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
                result = upload_theme(path, device)

            self.assertEqual(result["sha256"], digest)
            self.assertEqual(len(ChunkedConnection.instances), 3)
            first_request, second_request, commit_request = ChunkedConnection.instances
            self.assertEqual(first_request.method, "PUT")
            self.assertEqual(first_request.body, first)
            self.assertEqual(first_request.headers["X-HWT-Chunk-Offset"], "0")
            self.assertEqual(first_request.headers["X-HWT-Chunk-SHA256"], hashlib.sha256(first).hexdigest())
            self.assertEqual(first_request.headers["X-Content-SHA256"], digest)
            self.assertEqual(second_request.body, second)
            self.assertEqual(second_request.headers["X-HWT-Chunk-Offset"], str(CHUNK_SIZE))
            self.assertEqual(second_request.headers["X-HWT-Chunk-SHA256"], hashlib.sha256(second).hexdigest())
            self.assertEqual(second_request.headers["X-HWT-Total-Size"], str(len(content)))
            self.assertEqual(second_request.headers["X-HWT-File-Name"], "%E5%88%86%E5%9D%97%E4%B8%BB%E9%A2%98.hwt")
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

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection):
                with self.assertRaisesRegex(PhoneTransferError, "偏移量不一致") as raised:
                    upload_theme(path, device)

            self.assertEqual(raised.exception.code, "bad_response")
            self.assertEqual([request.method for request in ChunkedConnection.instances], ["PUT", "GET"])

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
            self.assertEqual(
                [request.method for request in ChunkedConnection.instances],
                ["PUT", "PUT", "POST", "GET", "GET"],
            )
            self.assertEqual(sum(request.method == "PUT" for request in ChunkedConnection.instances), 2)

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

    def test_upload_refuses_file_changed_after_hashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.hwt"
            path.write_bytes(b"original")
            before = path.stat()

            def hash_then_mutate(_path, *, cancelled=None):
                path.write_bytes(b"changed")
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
                return "0" * 64

            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")
            with patch("hwtstudio.phone_transfer.sha256_file", side_effect=hash_then_mutate):
                with self.assertRaisesRegex(PhoneTransferError, "发生变化") as raised:
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
            ):
                with self.assertRaisesRegex(PhoneTransferError, "发送后") as raised:
                    upload_theme(path, device)
            self.assertEqual(raised.exception.code, "file_changed")

    def test_malformed_remote_protocol_is_reported_as_bad_response(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token", features=["device_profile"])
        FakeHttpConnection.response_payload = {"protocol": "not-a-number"}
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
            with self.assertRaisesRegex(PhoneTransferError, "协议版本") as raised:
                probe_phone(device.host, device.port)
        self.assertEqual(raised.exception.code, "bad_response")

    def test_remote_text_fields_are_bounded_and_errors_are_single_line(self):
        FakeHttpConnection.response_payload = {
            "protocol": 1,
            "device_id": "phone-1",
            "name": "x" * 513,
        }
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
            with self.assertRaisesRegex(PhoneTransferError, "过长") as raised:
                probe_phone("127.0.0.1")
        self.assertEqual(raised.exception.code, "bad_response")

        error = _error_from_response(500, {"message": "x" * 600 + "\n第二行\x00", "code": "remote"})
        self.assertNotIn("\n", str(error))
        self.assertNotIn("\x00", str(error))
        self.assertLessEqual(len(str(error)), MAX_REMOTE_ERROR_CHARS + 3)

    def test_oversized_remote_response_is_rejected(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1")
        FakeHttpConnection.response_payload = {
            "protocol": 1,
            "device_id": "phone-1",
            "padding": "x" * MAX_RESPONSE_BYTES,
        }
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
            with self.assertRaisesRegex(PhoneTransferError, "过大") as raised:
                probe_phone(device.host, device.port)
        self.assertEqual(raised.exception.code, "bad_response")

    def test_invalid_remote_content_length_is_rejected(self):
        FakeHttpConnection.response_payload = {"protocol": 1}
        FakeHttpConnection.response_headers = {"Content-Length": "not-a-number"}
        try:
            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
                with self.assertRaisesRegex(PhoneTransferError, "响应长度") as raised:
                    probe_phone("127.0.0.1")
            self.assertEqual(raised.exception.code, "bad_response")
        finally:
            FakeHttpConnection.response_headers = {}

    def test_truncated_remote_response_is_rejected(self):
        FakeHttpConnection.response_payload = {"protocol": 1}
        body_length = len(json.dumps(FakeHttpConnection.response_payload).encode("utf-8"))
        FakeHttpConnection.response_headers = {"Content-Length": str(body_length + 1)}
        try:
            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
                with self.assertRaisesRegex(PhoneTransferError, "长度与声明不一致") as raised:
                    probe_phone("127.0.0.1")
            self.assertEqual(raised.exception.code, "bad_response")
        finally:
            FakeHttpConnection.response_headers = {}

    def test_missing_remote_device_id_is_reported_as_bad_response(self):
        FakeHttpConnection.response_payload = {"protocol": 1}
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
            with self.assertRaisesRegex(PhoneTransferError, "device_id") as raised:
                probe_phone("127.0.0.1")
        self.assertEqual(raised.exception.code, "bad_response")

    def test_malformed_pair_token_is_reported_as_bad_response(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1")
        FakeHttpConnection.response_payload = {"protocol": 1, "token": {}}
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
            with self.assertRaisesRegex(PhoneTransferError, "token") as raised:
                pair_phone(device, "123456")
        self.assertEqual(raised.exception.code, "bad_response")

    def test_upload_rejects_mismatched_remote_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.hwt"
            path.write_bytes(b"payload")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            FakeHttpConnection.response_payload = {"size": 999, "sha256": digest}
            device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token")
            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
                with self.assertRaisesRegex(PhoneTransferError, "大小") as raised:
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
            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
                with self.assertRaisesRegex(PhoneTransferError, "overwritten") as raised:
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
                with self.subTest(missing=missing):
                    with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
                        with self.assertRaisesRegex(PhoneTransferError, missing) as raised:
                            upload_theme(path, device)
                    self.assertEqual(raised.exception.code, "bad_response")

    def test_malformed_profile_fields_are_rejected_without_partial_state(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token", features=["device_profile"])
        FakeHttpConnection.response_payload = {"sdk_int": "not-a-number", "installed_packages": "bad"}
        with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
            with self.assertRaisesRegex(PhoneTransferError, "SDK") as raised:
                fetch_phone_profile(device)
        self.assertEqual(raised.exception.code, "bad_response")
        self.assertIsNone(device.profile)

    def test_profile_rejects_boolean_sdk_and_non_string_package(self):
        device = PhoneDevice("phone-1", "测试手机", "127.0.0.1", token="token", features=["device_profile"])
        for payload, message in (
            ({"sdk_int": True}, "SDK"),
            ({"sdk_int": 36, "installed_packages": ["com.example", 7]}, "列表"),
            ({"sdk_int": 36, "model": 7}, "model"),
        ):
            FakeHttpConnection.response_payload = payload
            with self.subTest(message=message):
                with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", FakeHttpConnection):
                    with self.assertRaisesRegex(PhoneTransferError, message) as raised:
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

    def test_registry_load_fails_closed_when_lock_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            with patch("hwtstudio.phone_transfer._interprocess_lock", side_effect=OSError("locked")):
                self.assertEqual(PhoneRegistry(path).load(), {})

    def test_registry_lock_has_bounded_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.json"
            with (
                patch("hwtstudio.phone_transfer.time.monotonic", side_effect=[0.0, 6.0]),
                patch("hwtstudio.phone_transfer.msvcrt.locking", side_effect=OSError("busy")),
            ):
                with self.assertRaisesRegex(OSError, "超时"):
                    with _interprocess_lock(path):
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
            registry.update(PhoneDevice("phone-1", "旧名称", "10.0.0.2", token="secret"))
            with patch("hwtstudio.phone_transfer.socket.socket", FakeDiscoverySocket):
                devices = discover_phones(timeout=0.01, registry=registry)
            self.assertEqual(len(devices), 1)
            self.assertEqual(devices[0].host, "10.0.0.8")
            self.assertEqual(devices[0].token, "secret")
            self.assertEqual(registry.load()["phone-1"].host, "10.0.0.8")

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
            ):
                with self.assertRaises(TransferCancelled):
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

            with patch("hwtstudio.phone_transfer.http.client.HTTPConnection", ChunkedConnection):
                with self.assertRaisesRegex(PhoneTransferError, "未知的传输状态") as raised:
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
