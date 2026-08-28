"""电脑出码、手机扫码的配对服务。

电脑在配对对话框内监听一个短生命周期的 HTTP 端口，二维码内容为
``hwtstudio://<电脑IP>:<端口>?s=<会话令牌>``。手机助手扫码解析后，
把自己的地址与当前配对码 POST 到该端口注册；电脑收到后把手机加入
配对列表并自动填入配对码，用户无需手动输入 IP。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, Signal

LOGGER = logging.getLogger(__name__)

QR_PAIRING_PORT = 48624
SESSION_TTL_S = 300
MAX_REGISTER_BYTES = 4096


def make_session() -> str:
    return uuid.uuid4().hex


def qr_connect_url(host: str, port: int = QR_PAIRING_PORT, session: str = "") -> str:
    wrapped = f"[{host}]" if ":" in host else host
    query = f"?s={session}" if session else ""
    return f"hwtstudio://{wrapped}:{port}{query}"


class _PairingHandler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    @property
    def pairing_server(self) -> QrPairingServer:
        # start() 里通过 httpd.server 注入的 QrPairingServer 实例。
        return self.server.server  # type: ignore[attr-defined]

    def log_message(self, _format: str, *args):  # 静默访问日志
        LOGGER.debug("qr pairing %s", args)

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/ping":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"code": "not_found"})

    def do_POST(self):
        if self.path != "/api/v1/register":
            self._json(404, {"code": "not_found"})
            return
        raw_length = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw_length)
        except ValueError:
            self._json(400, {"code": "invalid_body"})
            return
        if length <= 0 or length > MAX_REGISTER_BYTES:
            self._json(400, {"code": "invalid_body"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"code": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"code": "invalid_json"})
            return
        error = self.pairing_server.register(payload, self.client_address[0])
        if error:
            self._json(400, {"code": error})
        else:
            self._json(200, {"ok": True})


class QrPairingServer(QObject):
    """短生命周期注册服务：接收手机扫码后的注册请求并通知 UI。"""

    device_registered = Signal(object)  # PhoneDevice

    def __init__(self, *, port: int = QR_PAIRING_PORT, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = port

    @property
    def port(self) -> int:
        return self._port

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer(("0.0.0.0", self._port), _PairingHandler)
        httpd.server = self  # type: ignore[attr-defined]
        self._httpd = httpd
        self._port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    def new_session(self) -> str:
        """创建一个会话令牌，用作二维码内容里的 ``s`` 参数。"""
        token = make_session()
        now = time.monotonic()
        with self._lock:
            self._sessions = {
                existing: expiry
                for existing, expiry in self._sessions.items()
                if expiry > now
            }
            self._sessions[token] = now + SESSION_TTL_S
        return token

    def register(self, payload: dict, client_ip: str) -> str | None:
        """处理手机注册；返回错误码或 None。"""
        from .phone_transfer import HTTP_PORT, PhoneDevice

        session = payload.get("s") if isinstance(payload.get("s"), str) else None
        name = payload.get("name")
        device_id = payload.get("device_id")
        raw_port = payload.get("http_port", HTTP_PORT)
        pair_code = payload.get("pair_code") if isinstance(payload.get("pair_code"), str) else ""
        if not session or not name or not isinstance(name, str) or not name.strip():
            return "invalid_request"
        if not isinstance(device_id, str) or not device_id.strip():
            return "invalid_request"
        if isinstance(raw_port, bool) or not isinstance(raw_port, int) or not 1 <= raw_port <= 65535:
            return "invalid_port"
        now = time.monotonic()
        with self._lock:
            expiry = self._sessions.pop(session, None)
        if expiry is None or expiry <= now:
            return "session_expired"
        device = PhoneDevice(
            device_id=device_id.strip(),
            name=name.strip(),
            host=client_ip,
            port=raw_port,
            features=["qr_pairing"],
        )
        if pair_code:
            # 配对码作为独立字段传递；拼进设备显示名会允许恶意客户端伪造
            # “配对码”文本欺骗自动填充逻辑。
            device.pair_code = pair_code
        LOGGER.info("手机扫码注册：%s @ %s:%s", device.name, client_ip, raw_port)
        self.device_registered.emit(device)
        return None


def make_qr_image(url: str, size: int = 320):
    """生成二维码 PIL 图像（用于 UI 展示），缩放至 *size* 像素。"""
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return image.resize((size, size))
