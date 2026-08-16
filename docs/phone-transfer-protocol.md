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
  "app_version": "0.1.5",
  "features": ["device_profile", "transfer_cancel", "transfer_prepare", "transfer_chunked"]
}
```

### UDP 不可用时的 HTTP 发现回退

桌面端在本次 UDP 搜索没有得到任何有效设备时，才会使用 HTTP 回退，保持与 LocalSend v1
“组播失败后再探测局域网”的兼容思路。候选地址只来自桌面活动网卡的 IPv4 地址和掩码：

- 只展开系统判定为私有地址的网段；公共地址、非法地址和无法解析的掩码会跳过。
- 单个网段超过 256 个地址时跳过，所有网卡合计最多探测 256 个地址，不进行无界扫描。
- 上次连接过的手机地址会排在候选列表最前面，即使没有可用网卡子网也会直接探测一次；
  剩余预算再按网段顺序扫描，保证手机重新连 WiFi 换到偏大 IP 后仍能在预算内被发现。
- 对每个候选地址向 `48621` 发起 `GET /api/v1/status`；不使用域名，也不携带 Bearer Token。
- 发送/识别窗口使用 8 秒总预算：UDP 广播最多等待 1 秒，剩余预算全部留给 HTTP 兜底
  （16 个并发，单个请求最多等待 400 ms，可覆盖整个 /24 网段）；取消搜索会停止接收结果并
  取消尚未开始的候选请求。
- HTTP 响应必须通过与 UDP 响应相同的协议版本、设备标识、端口、文本和能力字段校验，
  畸形或非 200 响应只会被丢弃。

HTTP 回退只用于自动发现；用户填写的手动地址仍直接走状态探测，不会被自动加入扫描范围。

## HTTP API

服务仅在用户点击“开始接收”后的前台服务期间监听 `48621`。

### `GET /api/v1/status`

返回协议版本、设备 ID、设备名、APK 版本、运行状态和存储授权状态，不要求认证。

### `GET /api/v1/profile`

请求头为 `Authorization: Bearer <token>`。该可选接口由 `device_profile` 功能标记声明，返回：

```json
{
  "manufacturer": "HONOR",
  "model": "ELP-AN00",
  "android_release": "16",
  "sdk_int": 36,
  "os_name": "MagicOS_10.0.0",
  "build_display": "...",
  "installed_packages": ["com.android.settings", "com.tencent.mm"]
}
```

`installed_packages` 仅检查 HWT 资源目录涉及且在 Android 清单 `<queries>` 中声明的白名单，
不申请 `QUERY_ALL_PACKAGES`，也不返回其他应用。

### `POST /api/v1/pair`

请求必须包含 `Content-Length`，请求体不超过 16 KiB。请求 JSON：`{"code":"123456","client_name":"大雪主题编辑器"}`，其中两个字段均为字符串（`client_name` 可省略）；手机端会折叠空白、移除控制字符并限制为 60 个 Unicode 代码点。配对码有效期 5 分钟，
一分钟内连续失败 5 次后限速。成功返回设备信息和随机 256 位 `token`；每次成功配对后立即刷新配对码。

桌面端只会在设备 `device_id`、host 和端口都与最近一次确认记录一致时复用缓存的 token/profile；手机地址或端口变化后必须重新配对，避免把凭据发送给冒用稳定设备标识的发现响应。

缺少 JSON、JSON 格式错误或字段类型错误时返回 `400`，不会创建配对记录。

### `PUT /api/v1/themes/{urlencoded_filename}`

请求头：

- `Authorization: Bearer <token>`
- `Content-Type: application/octet-stream`
- `Content-Length: <bytes>`
- `X-Content-SHA256: <64 lowercase hex>`
- `X-HWT-Transfer-Id: <16-64 ASCII characters>`（可选；支持取消的桌面端会发送随机会话标识）

请求体是原始 HWT，最大 1 GiB。文件名会清理为安全的 `.hwt` 文件名，最终 UTF-8 长度不超过 200 字节。
APK 只接受包含根目录 `description.xml` 文件的有效 ZIP，并在读取 ZIP 中心目录时限制条目数量不超过 20,000、单个条目不超过 256 MiB、所有条目累计解压量不超过 512 MiB；外层 ZIP 会先审计本地文件数据区间、中心目录与本地文件头，再交给 ZIP 解析器，文件/目录路径前缀重叠、规范化重复路径、路径穿越、Unix 符号链接和超高压缩比也会在读取外层内容前拒绝；可识别的嵌套 ZIP 会流式写入受控临时文件后，同样检查中心目录、本地文件头和本地数据区间，再读取其条目内容；校验 SHA-256 后才以临时文件方式替换目标文件。成功返回：

```json
{
  "transfer_id": "same-session-id",
  "stored_name": "主题.hwt",
  "destination": "Honor/Themes/主题.hwt",
  "size": 64875407,
  "sha256": "...",
  "overwritten": false,
  "theme_app_opened": false
}
```

当请求带有 `X-HWT-Transfer-Id` 时，新版助手会在成功响应中回显相同的 `transfer_id`；桌面端会校验该字段。
旧版助手可能省略该字段，桌面端仍保留完整 PUT 兼容路径。

`overwritten` 和 `theme_app_opened` 是必需的布尔字段。接收服务在仍有 HTTP 请求处理时不会因 30 分钟空闲计时而停止。
一次安装开始后会固定当时选定的 SAF 目录；授权切换不会把同一文件拆分写入不同存储位置。

桌面端会拒绝超过 2 MiB、声明长度非法、声明长度与实际读取长度不一致，或 JSON 顶层结构/协议字段类型错误的手机响应；状态/profile/上传响应文本字段超过 512 个字符或包含控制字符时拒绝，远端错误文案会压缩为单行并限长。

错误响应统一包含 `code` 和中文 `message`。主要状态码为 `400` 请求错误、`401` 未配对、
`409` 正在上传、`413` 文件或配对请求过大、`422` HWT/摘要校验失败、`499` 上传已取消、`503` 目录授权失效、`507` 空间不足。

### `GET /api/v1/transfers/{id}`（可选状态扩展）

请求头为 `Authorization: Bearer <token>`。手机返回 `202 receiving` 表示会话仍在接收，返回
`202 committing` 表示文件已收齐、正在校验并安装，返回 `200 completed` 时附带原上传结果；不存在或旧版助手不支持时返回 `404`。
`receiving` 响应包含 `received`、`total`、`next_offset`，三者均为非负字节数，其中 `next_offset` 是下一块的写入偏移量；桌面端会在连接中断后先查询该接口，
避免对已经完成的主题重复发送文件。
所有 `transfers/{id}` 的成功状态响应都带有与 URL 相同的 `transfer_id`；桌面端在分块上传和断点恢复时会拒绝会话标识不一致的响应，避免把其他会话的进度当作当前上传进度。

### `POST /api/v1/transfers/{id}/prepare`（可选元数据预检）

当手机在 `features` 中声明 `transfer_prepare` 时，桌面端在发送任何 HWT 字节前提交一个不超过 16 KiB 的 JSON：

```json
{
  "file_name": "主题.hwt",
  "size": 64875407,
  "sha256": "..."
}
```

请求使用 `Authorization: Bearer <token>`，`{id}` 是随后完整 PUT 或分块上传复用的会话 ID。手机会先校验
文件名、大小、总 SHA-256、存储授权和当前忙状态，成功返回 `200 prepared` 并回显规范化后的元数据：

```json
{
  "state": "prepared",
  "transfer_id": "same-session-id",
  "file_name": "主题.hwt",
  "size": 64875407,
  "sha256": "..."
}
```

预检只是一阶段校验，不预先占用接收会话；真正上传时仍会再次检查会话、长度、摘要和 HWT 内容。
因此预检与上传之间的并发变化不会绕过现有校验。旧版助手返回 `404` 时桌面端直接回退到原始 PUT，
不会把能力声明当成必需的协议升级。

完整 PUT 在连接中断后会查询同一 `transfer_id` 的状态；如果仍处于接收会话，重试会复用已经
完成的预检并直接发送 PUT，不再次调用 `/prepare`，避免手机把同一活动会话误判为并发上传。

连接在发送字节时被远端重置（如 Windows 的 `[WinError 10054]`）同样进入上述幂等恢复流程；
自动重试耗尽后桌面端以专门的“上传被中断”提示引导重新接收/配对，而不再笼统报告“无法连接手机”；
随后会取消手机上残留的分块会话并回退到一次整包 `PUT`，保证旧版助手（如 0.2.4 APK）也能完成接收。

发送前桌面端会先用 `GET /api/v1/status` 实测手机并采用其最新 `features`（而不是配对记录里的
旧值），确保声明了 `transfer_prepare`/`transfer_chunked` 的助手走预检+分块路径。

### 分块上传（`transfer_chunked`）

当手机在 `features` 中声明 `transfer_chunked` 时，桌面端使用固定不超过 4 MiB 的分块，
而未声明该能力的旧版手机继续使用上一节的完整 `PUT`。分块上传不使用文件名路径，使用同一个会话 ID：

#### `PUT /api/v1/transfers/{id}`

请求头为：

- `Authorization: Bearer <token>`
- `Content-Type: application/octet-stream`
- `Content-Length: <chunk_bytes>`
- `X-Content-SHA256: <whole_file_sha256>`
- `X-HWT-Transfer-Id: <same id>`
- `X-HWT-Total-Size: <whole_file_bytes>`
- `X-HWT-Chunk-Offset: <non-negative byte offset>`
- `X-HWT-Chunk-SHA256: <chunk_sha256>`
- `X-HWT-File-Name: <urlencoded UTF-8 filename>`

手机严格要求分块从当前 `next_offset` 开始，单块不超过 4 MiB，并在写入临时文件前校验该块摘要。
成功接收返回 `202`：

```json
{
  "state": "receiving",
  "transfer_id": "random-session-id",
  "received": 4194304,
  "total": 64875407,
  "next_offset": 4194304
}
```

服务停止、用户取消或进程重启时会清理未完成的分块状态和受控命名的 `.uploading` 缓存文件；不符合普通文件条件的缓存对象不会被删除。

手机读取分块请求体时必须保持请求连接不关闭：分块 `202` 响应和后续 keep-alive 分块复用同一条 socket，关闭请求流会让电脑端收到“Remote end closed connection without response”。手机端 HTTP socket 读超时为 120 秒（NanoHTTPD 默认 5 秒），慢速 Wi-Fi 下的大分块/整包上传也能完成，空闲清理仍由 30 分钟计时任务负责。

#### `POST /api/v1/transfers/{id}/complete`

所有分块成功后，桌面端发送带 Bearer Token 且 `Content-Length: 0` 的空请求体。手机拒绝非空提交请求，
再校验整个临时文件的大小和 SHA-256，
然后复用完整上传路径的 HWT 校验、存储空间检查及原子安装逻辑。提交期间状态为 `committing`；
安装完成后返回与完整 `PUT` 相同的 `stored_name`、`destination`、`size`、`sha256`、`overwritten` 和
`theme_app_opened` 字段，并带有当前 `transfer_id`。提交响应丢失时，桌面端查询状态并等待 `completed`，不会重新追加最后一个分块。

### `DELETE /api/v1/transfers/{id}`（可选取消扩展）

请求头为 `Authorization: Bearer <token>`。`{id}` 必须是上传时的
`X-HWT-Transfer-Id`，手机在请求体解析完成、提交到 `Honor/Themes` 之前检查取消标记。
匹配活动会话时返回 `202`，会话不存在或已经进入安装提交阶段时返回 `404`。桌面端会忽略旧版助手
对该可选接口返回的 `404`，因此不影响只实现原始 Bearer v1 PUT 的手机端。

支持会话扩展的桌面端在连接中断时最多重试一次，并复用相同的 `X-HWT-Transfer-Id`、文件大小和 SHA-256。
Android 会在内存中保留最近 8 个成功会话；重试内容一致时直接返回原安装结果，若会话 ID 对应不同文件则返回
`409 transfer_id_reused`。缓存不跨应用重启持久化，旧版助手仍按原始 PUT 行为处理。
