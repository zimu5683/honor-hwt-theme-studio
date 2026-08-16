package io.github.zimu5683.hwttransfer

import android.content.Context
import android.net.Uri
import fi.iki.elonen.NanoHTTPD
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.LinkOption
import java.util.Collections
import java.util.LinkedHashMap
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

private val STALE_CHUNK_UPLOAD_PATTERN = Regex("^hwt_chunk_[A-Za-z0-9_-]{16,64}\\.uploading$")
private val liveChunkCommitFiles = Collections.synchronizedSet(mutableSetOf<File>())
private const val CHUNK_COPY_BUFFER_BYTES = 1024 * 1024

internal fun staleChunkUploadFiles(directory: File, protectedFile: File? = null): List<File> =
    staleChunkUploadFiles(directory, protectedFile?.let { setOf(it) } ?: emptySet())

internal fun staleChunkUploadFiles(directory: File, protectedFiles: Set<File>): List<File> =
    directory.listFiles().orEmpty().filter { file ->
        STALE_CHUNK_UPLOAD_PATTERN.matches(file.name) &&
            Files.isRegularFile(file.toPath(), LinkOption.NOFOLLOW_LINKS) &&
            protectedFiles.none { protected -> file.toPath() == protected.toPath() }
    }

internal fun cleanupStaleChunkUploadFiles(directory: File, protectedFile: File? = null): List<File> =
    cleanupStaleChunkUploadFiles(directory, protectedFile?.let { setOf(it) } ?: emptySet())

internal fun cleanupStaleChunkUploadFiles(directory: File, protectedFiles: Set<File>): List<File> =
    staleChunkUploadFiles(directory, protectedFiles).filterNot { it.delete() }

internal fun deleteParsedUploadFile(file: File): Boolean {
    val path = file.toPath()
    return !Files.exists(path, LinkOption.NOFOLLOW_LINKS) ||
        (Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) && file.delete())
}

internal fun validateEmptyRequestLength(declaredSize: Long) {
    if (declaredSize != 0L) {
        throw TransferException(400, "invalid_body", "分块提交请求必须为空")
    }
}

internal fun validateCachedTransfer(
    cached: InstallResult,
    requestedName: String,
    declaredSize: Long,
    expectedHash: String,
) {
    if (cached.storedName != requestedName ||
        cached.size != declaredSize ||
        !cached.sha256.equals(expectedHash, ignoreCase = true)
    ) {
        throw TransferException(409, "transfer_id_reused", "上传会话标识已用于其他文件")
    }
}

class ReceiverServer(
    context: Context,
    private val pairing: PairingManager,
    private val storage: ThemeStorage,
    private val onActivity: () -> Unit,
    private val onTransfer: (InstallResult) -> Unit,
) : NanoHTTPD(Protocol.HTTP_PORT) {
    private data class ChunkTransfer(
        val id: String,
        val file: File,
        val fileName: String,
        val totalSize: Long,
        val expectedHash: String,
        var received: Long = 0L,
    )

    private val appContext = context.applicationContext
    private val uploading = AtomicBoolean(false)
    private val stopping = AtomicBoolean(false)
    private val activeRequests = AtomicInteger(0)
    private val transferLock = Any()
    private var activeTransferId: String? = null
    private var cancelRequested = false
    private var chunkTransfer: ChunkTransfer? = null
    private var committingTransferId: String? = null
    private var committingTransferFile: File? = null
    private val completedTransfers = LinkedHashMap<String, InstallResult>(8, 0.75f, true)

    init {
        cleanupStaleChunkFiles()
    }

    fun hasActiveRequests(): Boolean = activeRequests.get() > 0

    /** Cancel transfer state and clean stale chunks without touching an active commit. */
    fun shutdownTransfers() {
        stopping.set(true)
        val (abandoned, protectedFile) = synchronized(transferLock) {
            val file = chunkTransfer?.file
            chunkTransfer = null
            activeTransferId = null
            cancelRequested = false
            if (committingTransferId == null) uploading.set(false)
            file to committingTransferFile
        }
        abandoned?.let { file ->
            if (!deleteRegularChunkFile(file)) {
                android.util.Log.w("ReceiverServer", "Unable to remove abandoned chunk file")
            }
        }
        cleanupStaleChunkFiles(protectedFile)
    }

    override fun serve(session: IHTTPSession): Response {
        activeRequests.incrementAndGet()
        return try {
            if (stopping.get()) {
                throw TransferException(503, "receiver_stopped", "接收服务正在停止")
            }
            onActivity()
            when {
                session.method == Method.GET && session.uri == "/api/v1/status" -> status()
                session.method == Method.GET && session.uri == "/api/v1/profile" -> profile(session)
                session.method == Method.GET && session.uri.startsWith("/api/v1/transfers/") -> transferStatus(session)
                session.method == Method.POST && session.uri == "/api/v1/pair" -> pair(session)
                session.method == Method.POST && session.uri.startsWith("/api/v1/transfers/") && session.uri.endsWith("/prepare") -> prepareTransfer(session)
                session.method == Method.POST && session.uri.endsWith("/complete") && session.uri.startsWith("/api/v1/transfers/") -> completeChunk(session)
                session.method == Method.DELETE && session.uri.startsWith("/api/v1/transfers/") -> cancel(session)
                session.method == Method.PUT && session.uri.startsWith("/api/v1/transfers/") -> uploadChunk(session)
                session.method == Method.PUT && session.uri.startsWith("/api/v1/themes/") -> upload(session)
                else -> json(404, JSONObject().put("code", "not_found").put("message", "接口不存在"))
            }
        } catch (exc: TransferException) {
            json(exc.status, JSONObject().put("code", exc.code).put("message", exc.message))
        } catch (exc: ResponseException) {
            json(exc.status.requestStatus, JSONObject().put("code", "invalid_request").put("message", "请求格式无效"))
        } catch (exc: Exception) {
            json(500, JSONObject().put("code", "internal_error").put("message", "手机处理失败"))
        } finally {
            activeRequests.decrementAndGet()
        }
    }

    private fun status(): Response = json(200, JSONObject()
        .put("service", "hwtstudio")
        .put("protocol", Protocol.VERSION)
        .put("device_id", pairing.deviceId)
        .put("name", android.os.Build.MODEL)
        .put("app_version", BuildConfig.VERSION_NAME)
        .put("features", JSONArray(Protocol.ADVERTISED_FEATURES))
        .put("running", true)
        .put("storage_ready", storage.isAvailable()))

    private fun pair(session: IHTTPSession): Response {
        val declaredSize = session.headers["content-length"]?.toLongOrNull()
            ?: throw TransferException(400, "missing_length", "配对请求缺少 Content-Length")
        if (declaredSize < 0L) {
            throw TransferException(400, "invalid_length", "配对请求长度无效")
        }
        if (declaredSize > Protocol.MAX_PAIR_BODY_BYTES) {
            throw TransferException(413, "pair_too_large", "配对请求过大")
        }
        val files = mutableMapOf<String, String>()
        session.parseBody(files)
        val requestBody = files["postData"]
            ?: throw TransferException(400, "missing_body", "配对请求缺少 JSON 内容")
        val request = Protocol.parsePairRequest(requestBody)
        val result = pairing.pair(request.code, request.clientName)
        ReceiverState.update { it.copy(pairCode = pairing.code, codeExpiresAt = pairing.codeExpiresAt, clients = pairing.clients()) }
        return json(200, JSONObject()
            .put("protocol", Protocol.VERSION)
            .put("device_id", pairing.deviceId)
            .put("name", android.os.Build.MODEL)
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("features", JSONArray(Protocol.ADVERTISED_FEATURES))
            .put("token", result.token))
    }

    private fun profile(session: IHTTPSession): Response {
        requireAuthorized(session)
        return json(200, DeviceProfile.json(appContext))
    }

    private fun requireAuthorized(session: IHTTPSession) {
        val bearer = session.headers["authorization"]?.removePrefix("Bearer ")
        if (!pairing.isAuthorized(bearer)) throw TransferException(401, "unauthorized", "配对令牌无效或已撤销")
    }

    private fun validateTransferId(value: String): String {
        if (!Regex("[A-Za-z0-9_-]{16,64}").matches(value)) {
            throw TransferException(400, "invalid_transfer_id", "上传会话标识无效")
        }
        return value
    }

    private fun transferId(uri: String): String {
        val encoded = uri.removePrefix("/api/v1/transfers/")
        return validateTransferId(Uri.decode(encoded))
    }

    private fun chunkCommitId(uri: String): String {
        val prefix = "/api/v1/transfers/"
        if (!uri.startsWith(prefix) || !uri.endsWith("/complete")) {
            throw TransferException(400, "invalid_transfer_id", "上传会话标识无效")
        }
        return validateTransferId(Uri.decode(uri.removePrefix(prefix).removeSuffix("/complete")))
    }

    private fun prepareTransferId(uri: String): String {
        val prefix = "/api/v1/transfers/"
        if (!uri.startsWith(prefix) || !uri.endsWith("/prepare")) {
            throw TransferException(400, "invalid_transfer_id", "上传会话标识无效")
        }
        return validateTransferId(Uri.decode(uri.removePrefix(prefix).removeSuffix("/prepare")))
    }

    private fun requiredLong(session: IHTTPSession, name: String): Long {
        return session.headers[name]?.toLongOrNull()
            ?: throw TransferException(400, "invalid_request", "请求缺少有效的 $name")
    }

    private fun requireChunkTransferHeader(session: IHTTPSession, id: String) {
        val headerId = session.headers["x-hwt-transfer-id"]?.trim()
            ?: throw TransferException(400, "missing_transfer_id", "请求缺少上传会话标识")
        if (headerId != id) {
            throw TransferException(400, "transfer_id_mismatch", "上传会话标识与请求路径不一致")
        }
    }

    /**
     * Copy the raw request body directly into [target]. This bypasses
     * NanoHTTPD's parseBody path, which would otherwise materialize the chunk
     * in a temp file, then copy it into a second temp file, then let us copy
     * it again into the chunk file.
     */
    private fun readChunkBody(session: IHTTPSession, target: File, expectedBytes: Long) {
        // 注意：绝不能关闭 NanoHTTPD 传入的 inputStream。它直接包着这次请求的
        // socket，一旦关闭，分块响应（202）和后续 keep-alive 请求都写不出去，
        // 电脑端会看到 “Remote end closed connection without response”。
        val input = session.inputStream
        FileOutputStream(target).use { output ->
            val buffer = ByteArray(CHUNK_COPY_BUFFER_BYTES)
            var copied = 0L
            while (copied < expectedBytes) {
                val count = input.read(buffer, 0, minOf(buffer.size.toLong(), expectedBytes - copied).toInt())
                if (count < 0) break
                if (count == 0) continue
                output.write(buffer, 0, count)
                copied += count
            }
            if (copied != expectedBytes || target.length() != expectedBytes) {
                throw TransferException(400, "incomplete_upload", "分块内容不完整")
            }
            output.fd.sync()
        }
    }

    private fun prepareTransfer(session: IHTTPSession): Response {
        requireAuthorized(session)
        val id = prepareTransferId(session.uri)
        val declaredSize = requiredLong(session, "content-length")
        if (declaredSize < 0L || declaredSize > Protocol.MAX_TRANSFER_PREPARE_BODY_BYTES) {
            throw TransferException(413, "prepare_too_large", "上传预检请求过大")
        }
        val files = mutableMapOf<String, String>()
        session.parseBody(files)
        val requestBody = files["postData"]
            ?: throw TransferException(400, "missing_body", "上传预检缺少 JSON 内容")
        val request = Protocol.parseTransferPrepare(requestBody)
        if (!storage.isAvailable()) {
            throw TransferException(503, "storage_unavailable", "Honor/Themes 目录尚未授权")
        }
        synchronized(transferLock) {
            if (uploading.get() || chunkTransfer != null || committingTransferId != null || activeTransferId != null) {
                throw TransferException(409, "busy", "手机正在接收另一个主题")
            }
        }
        return json(200, JSONObject()
            .put("state", "prepared")
            .put("transfer_id", id)
            .put("file_name", request.fileName)
            .put("size", request.totalSize)
            .put("sha256", request.sha256))
    }

    private fun discardChunkLocked(id: String): File? {
        val current = chunkTransfer?.takeIf { it.id == id } ?: return null
        chunkTransfer = null
        activeTransferId = null
        cancelRequested = false
        return current.file
    }

    private fun cancel(session: IHTTPSession): Response {
        requireAuthorized(session)
        val id = transferId(session.uri)
        var abandonedFile: File? = null
        val accepted = synchronized(transferLock) {
            if (activeTransferId == id) {
                if (committingTransferId == id) {
                    false
                } else {
                    abandonedFile = discardChunkLocked(id)
                    if (abandonedFile == null) cancelRequested = true
                    true
                }
            } else {
                false
            }
        }
        abandonedFile?.let { file ->
            if (!deleteRegularChunkFile(file)) {
                android.util.Log.w("ReceiverServer", "Unable to remove cancelled chunk file")
            }
        }
        return if (accepted) {
            json(202, JSONObject().put("code", "cancel_requested").put("transfer_id", id))
        } else {
            json(404, JSONObject().put("code", "transfer_not_found").put("message", "上传会话不存在"))
        }
    }

    private fun reserveChunk(
        id: String,
        fileName: String,
        totalSize: Long,
        expectedHash: String,
        offset: Long,
    ): ChunkTransfer {
        return synchronized(transferLock) {
            if (stopping.get()) throw TransferException(503, "receiver_stopped", "接收服务正在停止")
            if (uploading.get()) throw TransferException(409, "busy", "手机正在接收另一个数据块")
            val current = chunkTransfer
            if (current == null) {
                if (activeTransferId != null || offset != 0L) {
                    throw TransferException(409, "unexpected_offset", "上传必须从偏移量 0 开始")
                }
                val temporary = File(appContext.cacheDir, "hwt_chunk_$id.uploading")
                if (Files.exists(temporary.toPath(), LinkOption.NOFOLLOW_LINKS)) {
                    if (!Files.isRegularFile(temporary.toPath(), LinkOption.NOFOLLOW_LINKS) || !temporary.delete()) {
                        throw TransferException(503, "storage_unavailable", "无法清理旧的分块临时文件")
                    }
                }
                ChunkTransfer(id, temporary, fileName, totalSize, expectedHash).also {
                    chunkTransfer = it
                    activeTransferId = id
                    cancelRequested = false
                    uploading.set(true)
                }
            } else {
                if (current.id != id) throw TransferException(409, "busy", "手机正在接收另一个主题")
                if (current.fileName != fileName || current.totalSize != totalSize ||
                    !current.expectedHash.equals(expectedHash, ignoreCase = true)
                ) {
                    throw TransferException(409, "transfer_mismatch", "分块会话参数不一致")
                }
                if (offset != current.received) {
                    throw TransferException(409, "unexpected_offset", "分块偏移量与手机记录不一致")
                }
                uploading.set(true)
                current
            }
        }
    }

    private fun uploadChunk(session: IHTTPSession): Response {
        requireAuthorized(session)
        val id = transferId(session.uri)
        requireChunkTransferHeader(session, id)
        val declaredSize = requiredLong(session, "content-length")
        val totalSize = requiredLong(session, "x-hwt-total-size")
        val offset = requiredLong(session, "x-hwt-chunk-offset")
        Protocol.validateUploadLength(totalSize)
        if (declaredSize <= 0L || declaredSize > Protocol.MAX_TRANSFER_CHUNK_BYTES) {
            throw TransferException(400, "invalid_chunk", "分块大小无效")
        }
        if (offset < 0L || offset > totalSize || declaredSize > totalSize - offset) {
            throw TransferException(400, "invalid_chunk", "分块范围无效")
        }
        val expectedHash = session.headers["x-content-sha256"]?.lowercase()
            ?: throw TransferException(400, "missing_hash", "请求缺少 X-Content-SHA256")
        if (!expectedHash.matches(Regex("[0-9a-f]{64}"))) {
            throw TransferException(400, "invalid_hash", "X-Content-SHA256 格式错误")
        }
        val chunkHash = session.headers["x-hwt-chunk-sha256"]?.lowercase()
            ?: throw TransferException(400, "missing_chunk_hash", "请求缺少分块 SHA-256")
        if (!chunkHash.matches(Regex("[0-9a-f]{64}"))) {
            throw TransferException(400, "invalid_chunk_hash", "分块 SHA-256 格式错误")
        }
        val requestedName = session.headers["x-hwt-file-name"]
            ?.let { Uri.decode(it) }
            ?: throw TransferException(400, "missing_filename", "请求缺少主题文件名")
        val fileName = Protocol.safeFileName(requestedName)
        val state = reserveChunk(id, fileName, totalSize, expectedHash, offset)
        val incoming = File(appContext.cacheDir, "hwt_incoming_${System.nanoTime()}.part")
        try {
            readChunkBody(session, incoming, declaredSize)
            if (!Protocol.sha256(incoming).equals(chunkHash, ignoreCase = true)) {
                throw TransferException(422, "hash_mismatch", "分块 SHA-256 校验失败")
            }
            synchronized(transferLock) {
                val current = chunkTransfer?.takeIf { it.id == id }
                    ?: throw TransferException(499, "cancelled", "上传已取消")
                if (cancelRequested) throw TransferException(499, "cancelled", "上传已取消")
                if (current.received != offset) {
                    throw TransferException(409, "unexpected_offset", "分块偏移量与手机记录不一致")
                }
                if (current.file.length() != current.received) {
                    discardChunkLocked(id)?.delete()
                    throw TransferException(503, "storage_unavailable", "分块临时文件状态异常")
                }
                try {
                    FileInputStream(incoming).use { input ->
                        FileOutputStream(current.file, true).use { output ->
                            input.copyTo(output)
                        }
                    }
                } catch (exc: Exception) {
                    discardChunkLocked(id)?.delete()
                    throw TransferException(503, "storage_unavailable", "无法保存上传分块").also {
                        it.addSuppressed(exc)
                    }
                }
                current.received += declaredSize
                if (current.file.length() != current.received) {
                    discardChunkLocked(id)?.delete()
                    throw TransferException(503, "storage_unavailable", "无法保存上传分块")
                }
            }
            return json(202, JSONObject()
                .put("state", "receiving")
                .put("transfer_id", id)
                .put("received", state.received)
                .put("total", state.totalSize)
                .put("next_offset", state.received))
        } finally {
            incoming.delete()
            synchronized(transferLock) { uploading.set(false) }
        }
    }

    private fun completeChunk(session: IHTTPSession): Response {
        requireAuthorized(session)
        val id = chunkCommitId(session.uri)
        validateEmptyRequestLength(requiredLong(session, "content-length"))
        val cached = cachedTransfer(id)
        if (cached != null) return installResponse(cached, transferId = id)
        val state = synchronized(transferLock) {
            // Recheck under the state lock so a retry cannot observe the hand-off gap.
            completedTransfers[id]?.let { return installResponse(it, transferId = id) }
            if (committingTransferId == id) {
                return json(202, JSONObject()
                    .put("state", "committing")
                    .put("transfer_id", id))
            }
            if (uploading.get()) throw TransferException(409, "busy", "手机正在接收另一个数据块")
            val current = chunkTransfer?.takeIf { it.id == id }
                ?: throw TransferException(404, "transfer_not_found", "上传会话不存在")
            if (cancelRequested) {
                discardChunkLocked(id)?.delete()
                throw TransferException(499, "cancelled", "上传已取消")
            }
            if (current.received != current.totalSize) {
                throw TransferException(409, "incomplete_upload", "上传分块尚未全部收到")
            }
            committingTransferId = id
            committingTransferFile = current.file
            liveChunkCommitFiles.add(current.file)
            chunkTransfer = null
            activeTransferId = id
            cancelRequested = false
            uploading.set(true)
            current
        }
        try {
            val result = storage.installChunked(state.file, state.fileName, state.expectedHash)
            rememberCompleted(id, result)
            runCatching { onTransfer(result) }
                .onFailure { android.util.Log.e("ReceiverServer", "Transfer callback failed", it) }
            return installResponse(result, transferId = id)
        } finally {
            state.file.delete()
            val cleanupAfterCommit = synchronized(transferLock) {
                if (committingTransferId == id) {
                    committingTransferId = null
                    committingTransferFile = null
                }
                if (activeTransferId == id) activeTransferId = null
                cancelRequested = false
                uploading.set(false)
                liveChunkCommitFiles.remove(state.file)
                stopping.get()
            }
            if (cleanupAfterCommit) cleanupStaleChunkFiles()
        }
    }

    private fun transferStatus(session: IHTTPSession): Response {
        requireAuthorized(session)
        val id = transferId(session.uri)
        val completed = cachedTransfer(id)
        if (completed != null) {
            return installResponse(completed, 200, "completed", id)
        }
        val committing = synchronized(transferLock) { committingTransferId == id }
        if (committing) {
            return json(202, JSONObject()
                .put("state", "committing")
                .put("transfer_id", id))
        }
        val chunk = synchronized(transferLock) { chunkTransfer?.takeIf { it.id == id } }
        if (chunk != null) {
            return json(202, JSONObject()
                .put("state", "receiving")
                .put("transfer_id", id)
                .put("received", chunk.received)
                .put("total", chunk.totalSize)
                .put("next_offset", chunk.received))
        }
        val receiving = synchronized(transferLock) { activeTransferId == id }
        return if (receiving) {
            json(202, JSONObject().put("state", "receiving").put("transfer_id", id))
        } else {
            json(404, JSONObject().put("state", "not_found").put("transfer_id", id))
        }
    }

    private fun acquireFullTransfer(id: String?): Boolean {
        return synchronized(transferLock) {
            if (stopping.get() || uploading.get() || chunkTransfer != null || committingTransferId != null || activeTransferId != null) {
                false
            } else {
                uploading.set(true)
                activeTransferId = id
                cancelRequested = false
                true
            }
        }
    }

    private fun claimTransferForInstall(id: String?): Boolean {
        if (stopping.get()) return false
        if (id == null) return true
        return synchronized(transferLock) {
            if (stopping.get() || activeTransferId != id || cancelRequested) {
                if (activeTransferId == id) {
                    activeTransferId = null
                    cancelRequested = false
                }
                false
            } else {
                committingTransferId = id
                activeTransferId = null
                cancelRequested = false
                true
            }
        }
    }

    private fun clearTransfer(id: String?) {
        synchronized(transferLock) {
            if (id != null) {
                if (activeTransferId == id) activeTransferId = null
                if (committingTransferId == id) committingTransferId = null
                if (activeTransferId == null) cancelRequested = false
            }
            uploading.set(false)
        }
    }

    private fun cleanupStaleChunkFiles(protectedFile: File? = null) {
        val protectedFiles = synchronized(liveChunkCommitFiles) {
            liveChunkCommitFiles.toMutableSet().apply {
                protectedFile?.let(::add)
            }
        }
        runCatching { cleanupStaleChunkUploadFiles(appContext.cacheDir, protectedFiles) }
            .getOrElse {
                android.util.Log.w("ReceiverServer", "Unable to enumerate stale chunk files", it)
                return
            }
            .forEach { file ->
                android.util.Log.w("ReceiverServer", "Unable to remove stale chunk file: ${file.name}")
            }
    }

    private fun deleteRegularChunkFile(file: File): Boolean {
        val path = file.toPath()
        return Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) && file.delete()
    }

    private fun cachedTransfer(id: String?): InstallResult? {
        if (id == null) return null
        return synchronized(transferLock) { completedTransfers[id] }
    }

    private fun rememberCompleted(id: String, result: InstallResult) {
        synchronized(transferLock) {
            completedTransfers[id] = result
            while (completedTransfers.size > 8) {
                val iterator = completedTransfers.entries.iterator()
                if (iterator.hasNext()) {
                    iterator.next()
                    iterator.remove()
                }
            }
        }
    }

    private fun installResponse(
        result: InstallResult,
        status: Int = 201,
        state: String? = null,
        transferId: String? = null,
    ): Response = json(status, JSONObject()
        .apply {
            if (state != null) put("state", state)
            if (transferId != null) put("transfer_id", transferId)
        }
        .put("stored_name", result.storedName)
        .put("destination", result.destination)
        .put("size", result.size)
        .put("sha256", result.sha256)
        .put("overwritten", result.overwritten)
        .put("theme_app_opened", ReceiverState.activityVisible))

    private fun upload(session: IHTTPSession): Response {
        requireAuthorized(session)
        val declaredSize = session.headers["content-length"]?.toLongOrNull()
            ?: throw TransferException(400, "missing_length", "请求缺少 Content-Length")
        Protocol.validateUploadLength(declaredSize)
        val expectedHash = session.headers["x-content-sha256"]?.lowercase()
            ?: throw TransferException(400, "missing_hash", "请求缺少 X-Content-SHA256")
        if (!expectedHash.matches(Regex("[0-9a-f]{64}"))) {
            throw TransferException(400, "invalid_hash", "X-Content-SHA256 格式错误")
        }
        val transferId = session.headers["x-hwt-transfer-id"]?.trim()?.takeIf { it.isNotEmpty() }
            ?.let(::validateTransferId)
        if (!acquireFullTransfer(transferId)) {
            throw TransferException(409, "busy", "手机正在接收另一个主题")
        }
        var temporary: File? = null
        try {
            val encodedName = session.uri.removePrefix("/api/v1/themes/")
            val name = Protocol.safeFileName(Uri.decode(encodedName))
            val files = mutableMapOf<String, String>()
            session.parseBody(files)
            val tempPath = files["content"] ?: files["postData"]
                ?: throw TransferException(400, "missing_body", "没有收到文件内容")
            val tempFile = File(tempPath)
            temporary = tempFile
            if (!tempFile.isFile || tempFile.length() != declaredSize) {
                throw TransferException(400, "incomplete_upload", "上传内容不完整")
            }
            val cached = cachedTransfer(transferId)
            if (cached != null) {
                validateCachedTransfer(cached, name, declaredSize, expectedHash)
                if (!Protocol.sha256(tempFile).equals(cached.sha256, ignoreCase = true)) {
                    throw TransferException(422, "hash_mismatch", "重试文件的 SHA-256 与原上传不一致")
                }
                return installResponse(cached, transferId = transferId)
            }
            if (!claimTransferForInstall(transferId)) {
                throw TransferException(499, "cancelled", "上传已取消")
            }
            val result = storage.install(tempFile, name, expectedHash)
            if (transferId != null) rememberCompleted(transferId, result)
            runCatching { onTransfer(result) }
                .onFailure { android.util.Log.e("ReceiverServer", "Transfer callback failed", it) }
            return installResponse(result, transferId = transferId)
        } finally {
            temporary?.let { file ->
                if (!deleteParsedUploadFile(file)) {
                    android.util.Log.w("ReceiverServer", "Unable to remove parsed upload file: ${file.name}")
                }
            }
            clearTransfer(transferId)
        }
    }

    private fun json(status: Int, body: JSONObject): Response = newFixedLengthResponse(
        HttpStatus(status), "application/json; charset=utf-8", body.toString(),
    ).apply {
        addHeader("Cache-Control", "no-store")
    }

    private class HttpStatus(private val code: Int) : Response.IStatus {
        override fun getRequestStatus(): Int = code
        override fun getDescription(): String = code.toString()
    }
}
