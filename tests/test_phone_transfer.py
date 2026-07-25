from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from hwtstudio.phone_transfer import (
    DISCOVERY_REQUEST,
    PhoneDevice,
    PhoneRegistry,
    discover_phones,
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
        self._json(200, {
            "protocol": 1,
            "device_id": "phone-1",
            "name": "测试手机",
            "app_version": "0.1.0",
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
        })


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
            }).encode()
            return body, ("10.0.0.8", 48620)
        raise socket.timeout()

    def close(self):
        pass


class PhoneTransferTests(unittest.TestCase):
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

    def test_safe_filename(self):
        self.assertEqual(safe_hwt_filename("../我的 主题.hwt"), "我的_主题.hwt")
        self.assertEqual(safe_hwt_filename("theme"), "theme.hwt")


if __name__ == "__main__":
    unittest.main()
