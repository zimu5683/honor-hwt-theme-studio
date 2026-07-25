package io.github.zimu5683.hwttransfer

import android.content.Context
import fi.iki.elonen.NanoHTTPD
import org.json.JSONObject
import java.io.File
import java.net.URLDecoder
import java.util.concurrent.atomic.AtomicBoolean

class ReceiverServer(
    context: Context,
    private val pairing: PairingManager,
    private val storage: ThemeStorage,
    private val onActivity: () -> Unit,
    private val onTransfer: (InstallResult) -> Unit,
) : NanoHTTPD(Protocol.HTTP_PORT) {
    private val appContext = context.applicationContext
    private val uploading = AtomicBoolean(false)

    override fun serve(session: IHTTPSession): Response {
        onActivity()
        return try {
            when {
                session.method == Method.GET && session.uri == "/api/v1/status" -> status()
                session.method == Method.POST && session.uri == "/api/v1/pair" -> pair(session)
                session.method == Method.PUT && session.uri.startsWith("/api/v1/themes/") -> upload(session)
                else -> json(404, JSONObject().put("code", "not_found").put("message", "接口不存在"))
            }
        } catch (exc: TransferException) {
            json(exc.status, JSONObject().put("code", exc.code).put("message", exc.message))
        } catch (exc: ResponseException) {
            json(exc.status.requestStatus, JSONObject().put("code", "invalid_request").put("message", exc.message))
        } catch (exc: Exception) {
            json(500, JSONObject().put("code", "internal_error").put("message", "手机处理失败：${exc.message}"))
        }
    }

    private fun status(): Response = json(200, JSONObject()
        .put("service", "hwtstudio")
        .put("protocol", Protocol.VERSION)
        .put("device_id", pairing.deviceId)
        .put("name", android.os.Build.MODEL)
        .put("app_version", BuildConfig.VERSION_NAME)
        .put("running", true)
        .put("storage_ready", storage.isAvailable()))

    private fun pair(session: IHTTPSession): Response {
        val files = mutableMapOf<String, String>()
        session.parseBody(files)
        val body = JSONObject(files["postData"] ?: "{}")
        val result = pairing.pair(body.optString("code"), body.optString("client_name"))
        ReceiverState.update { it.copy(pairCode = pairing.code, codeExpiresAt = pairing.codeExpiresAt, clients = pairing.clients()) }
        return json(200, JSONObject()
            .put("protocol", Protocol.VERSION)
            .put("device_id", pairing.deviceId)
            .put("name", android.os.Build.MODEL)
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("token", result.token))
    }

    private fun upload(session: IHTTPSession): Response {
        val bearer = session.headers["authorization"]?.removePrefix("Bearer ")
        if (!pairing.isAuthorized(bearer)) throw TransferException(401, "unauthorized", "配对令牌无效或已撤销")
        val declaredSize = session.headers["content-length"]?.toLongOrNull()
            ?: throw TransferException(400, "missing_length", "请求缺少 Content-Length")
        if (declaredSize < 0 || declaredSize > Protocol.MAX_FILE_SIZE) {
            throw TransferException(413, "too_large", "HWT 文件超过 1 GiB 上限")
        }
        val expectedHash = session.headers["x-content-sha256"]?.lowercase()
            ?: throw TransferException(400, "missing_hash", "请求缺少 X-Content-SHA256")
        if (!expectedHash.matches(Regex("[0-9a-f]{64}"))) {
            throw TransferException(400, "invalid_hash", "X-Content-SHA256 格式错误")
        }
        if (!uploading.compareAndSet(false, true)) {
            throw TransferException(409, "busy", "手机正在接收另一个主题")
        }
        try {
            val encodedName = session.uri.removePrefix("/api/v1/themes/")
            val name = Protocol.safeFileName(URLDecoder.decode(encodedName, Charsets.UTF_8.name()))
            val files = mutableMapOf<String, String>()
            session.parseBody(files)
            val tempPath = files["content"] ?: files["postData"]
                ?: throw TransferException(400, "missing_body", "没有收到文件内容")
            val temporary = File(tempPath)
            if (!temporary.isFile || temporary.length() != declaredSize) {
                throw TransferException(400, "incomplete_upload", "上传内容不完整")
            }
            val result = storage.install(temporary, name, expectedHash)
            onTransfer(result)
            return json(201, JSONObject()
                .put("stored_name", result.storedName)
                .put("destination", result.destination)
                .put("size", result.size)
                .put("sha256", result.sha256)
                .put("overwritten", result.overwritten)
                .put("theme_app_opened", ReceiverState.activityVisible))
        } finally {
            uploading.set(false)
        }
    }

    private fun json(status: Int, body: JSONObject): Response = newFixedLengthResponse(
        HttpStatus(status), "application/json; charset=utf-8", body.toString(),
    ).apply {
        addHeader("Cache-Control", "no-store")
        addHeader("Connection", "close")
    }

    private class HttpStatus(private val code: Int) : Response.IStatus {
        override fun getRequestStatus(): Int = code
        override fun getDescription(): String = code.toString()
    }
}
