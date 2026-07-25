# HWT 手机传输协议 v1

本协议用于“大雪主题编辑器”与“荣耀主题传输助手”APK 在可信局域网内传输 HWT。
首版使用带 Bearer Token 的 HTTP，不提供 TLS，不应暴露到公网。

## 发现

- 电脑向 UDP `48620` 广播 UTF-8 文本 `HWTSTUDIO_DISCOVER_V1`。
- APK 向请求来源返回 JSON：

```json
{
  "service": "hwtstudio",
  "protocol": 1,
  "device_id": "stable-uuid",
  "name": "ELP-AN00",
  "http_port": 48621,
  "app_version": "0.1.1"
}
```

## HTTP API

服务仅在用户点击“开始接收”后的前台服务期间监听 `48621`。

### `GET /api/v1/status`

返回协议版本、设备 ID、设备名、APK 版本、运行状态和存储授权状态，不要求认证。

### `POST /api/v1/pair`

请求 JSON：`{"code":"123456","client_name":"大雪主题编辑器"}`。配对码有效期 5 分钟，
一分钟内连续失败 5 次后限速。成功返回设备信息和随机 256 位 `token`；每次成功配对后立即刷新配对码。

### `PUT /api/v1/themes/{urlencoded_filename}`

请求头：

- `Authorization: Bearer <token>`
- `Content-Type: application/octet-stream`
- `Content-Length: <bytes>`
- `X-Content-SHA256: <64 lowercase hex>`

请求体是原始 HWT，最大 1 GiB。APK 只接受包含根目录 `description.xml` 的有效 ZIP，
校验 SHA-256 后才以临时文件方式替换目标文件。成功返回：

```json
{
  "stored_name": "主题.hwt",
  "destination": "Honor/Themes/主题.hwt",
  "size": 64875407,
  "sha256": "...",
  "overwritten": false,
  "theme_app_opened": false
}
```

错误响应统一包含 `code` 和中文 `message`。主要状态码为 `400` 请求错误、`401` 未配对、
`409` 正在上传、`413` 文件过大、`422` HWT/摘要校验失败、`503` 目录授权失效、`507` 空间不足。
