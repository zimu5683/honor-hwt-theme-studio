"""QR 配对服务回归测试：注册流程、配对码独立字段、畸形请求防御。"""
from __future__ import annotations

import http.client
import json
import os
import time
import unittest
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication

from hwtstudio.phone_transfer import HTTP_PORT, PhoneDevice
from hwtstudio.qr_pairing import QrPairingServer, qr_connect_url


class QrPairingTests(unittest.TestCase):
    def setUp(self):
        self._app = QCoreApplication.instance() or QCoreApplication([])
        self.server = QrPairingServer(port=0)
        self.server.start()
        self.port = self.server.port
        self.received: list[PhoneDevice] = []
        self.server.device_registered.connect(self.received.append)

    def tearDown(self):
        self.server.stop()

    def _register(self, payload: dict, *, override_length: str | None = None):
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if override_length is not None:
            headers["Content-Length"] = override_length
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", "/api/v1/register", body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8"))
        finally:
            conn.close()

    def _wait_for_device(self) -> PhoneDevice | None:
        for _ in range(100):
            self._app.processEvents()
            if self.received:
                return self.received[0]
            time.sleep(0.02)
        return None

    def test_register_emits_device_with_pair_code_field(self):
        token = self.server.new_session()
        status, payload = self._register(
            {
                "s": token,
                "name": "我的荣耀手机",
                "device_id": "abcd1234",
                "http_port": 48621,
                "pair_code": "654321",
            }
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("ok"))
        device = self._wait_for_device()
        self.assertIsNotNone(device)
        self.assertEqual(device.name, "我的荣耀手机")
        self.assertEqual(device.pair_code, "654321")
        self.assertNotIn("配对码", device.name)
        self.assertEqual(device.host, "127.0.0.1")
        self.assertEqual(device.port, 48621)

    def test_register_without_pair_code_keeps_name_clean(self):
        token = self.server.new_session()
        status, _ = self._register(
            {"s": token, "name": "手机", "device_id": "id-1", "http_port": HTTP_PORT}
        )
        self.assertEqual(status, 200)
        device = self._wait_for_device()
        self.assertIsNotNone(device)
        self.assertEqual(device.name, "手机")
        self.assertEqual(device.pair_code, "")

    def test_register_rejects_unknown_session(self):
        status, payload = self._register(
            {"s": "deadbeef", "name": "手机", "device_id": "id-2", "http_port": HTTP_PORT}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("code"), "session_expired")

    def test_register_rejects_malformed_content_length(self):
        token = self.server.new_session()
        status, payload = self._register(
            {"s": token, "name": "手机", "device_id": "id-3", "http_port": HTTP_PORT},
            override_length="not-a-number",
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("code"), "invalid_body")

    def test_register_rejects_oversized_body(self):
        token = self.server.new_session()
        status, payload = self._register(
            {"s": token, "name": "x" * 8192, "device_id": "id-4", "http_port": HTTP_PORT}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("code"), "invalid_body")

    def test_qr_connect_url_wraps_ipv6(self):
        url = qr_connect_url("fe80::1", port=48624, session="tok")
        self.assertEqual(url, "hwtstudio://[fe80::1]:48624?s=tok")

    def test_ping_endpoint(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/ping", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertTrue(json.loads(response.read().decode("utf-8")).get("ok"))


if __name__ == "__main__":
    unittest.main()
