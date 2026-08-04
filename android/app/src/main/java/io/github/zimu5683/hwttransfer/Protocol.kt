package io.github.zimu5683.hwttransfer

import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.security.MessageDigest
import java.text.Normalizer
import java.util.HashSet
import java.util.zip.ZipFile

object Protocol {
    const val VERSION = 1
    const val DISCOVERY_PORT = 48620
    const val HTTP_PORT = 48621
    const val DISCOVERY_REQUEST = "HWTSTUDIO_DISCOVER_V1"
    const val FEATURE_TRANSFER_CANCEL = "transfer_cancel"
    const val FEATURE_TRANSFER_CHUNKED = "transfer_chunked"
    const val FEATURE_TRANSFER_PREPARE = "transfer_prepare"
    const val MAX_FILE_SIZE = 1024L * 1024L * 1024L
    const val MAX_FILE_NAME_BYTES = 200
    const val MAX_ARCHIVE_ENTRY_BYTES = 256L * 1024L * 1024L
    const val MAX_ARCHIVE_UNCOMPRESSED_BYTES = 512L * 1024L * 1024L
    const val MAX_ARCHIVE_ENTRIES = 20_000
    const val MAX_ARCHIVE_COMPRESSION_RATIO = 500.0
    const val MAX_CLIENT_NAME_CODE_POINTS = 60
    const val MAX_PAIR_BODY_BYTES = 16L * 1024L
    const val MAX_TRANSFER_PREPARE_BODY_BYTES = 16L * 1024L
    const val MAX_TRANSFER_CHUNK_BYTES = 4L * 1024L * 1024L
    const val FREE_SPACE_RESERVE_BYTES = 16L * 1024L * 1024L
    const val IDLE_TIMEOUT_MS = 30L * 60L * 1000L
    const val PAIR_CODE_TTL_MS = 5L * 60L * 1000L

    data class PairRequest(val code: String, val clientName: String)

    data class TransferPrepare(val fileName: String, val totalSize: Long, val sha256: String)

    fun parsePairRequest(raw: String): PairRequest {
        val body = try {
            JSONObject(raw)
        } catch (_: JSONException) {
            throw TransferException(400, "invalid_json", "配对请求 JSON 无效")
        }
        val code = body.opt("code")
        if (code !is String) {
            throw TransferException(400, "invalid_request", "配对请求缺少字符串 code")
        }
        val clientName = when {
            !body.has("client_name") || body.isNull("client_name") -> ""
            body.opt("client_name") is String -> body.optString("client_name")
            else -> throw TransferException(400, "invalid_request", "配对请求的 client_name 必须是字符串")
        }
        return PairRequest(code, clientName)
    }

    fun parseTransferPrepare(raw: String): TransferPrepare {
        val body = try {
            JSONObject(raw)
        } catch (_: JSONException) {
            throw TransferException(400, "invalid_json", "上传预检 JSON 无效")
        }
        val fileName = body.opt("file_name")
        if (fileName !is String || fileName.isBlank()) {
            throw TransferException(400, "invalid_request", "上传预检缺少字符串 file_name")
        }
        val size = when (val value = body.opt("size")) {
            is Int -> value.toLong()
            is Long -> value
            else -> throw TransferException(400, "invalid_request", "上传预检缺少整数 size")
        }
        validateUploadLength(size)
        val sha256 = body.opt("sha256")
        if (sha256 !is String || !sha256.matches(Regex("[0-9a-fA-F]{64}"))) {
            throw TransferException(400, "invalid_request", "上传预检缺少有效 SHA-256")
        }
        return TransferPrepare(safeFileName(fileName), size, sha256.lowercase())
    }

    fun safeFileName(input: String): String {
        var name = input.substringAfterLast('/').substringAfterLast('\\')
            .replace(Regex("[^\\p{L}\\p{N}_.-]+"), "_")
            .trim('.', '_')
        if (name.isBlank()) name = "theme"
        if (!name.endsWith(".hwt", ignoreCase = true)) name += ".hwt"
        val extension = name.takeLast(4)
        val stem = name.dropLast(4)
        val maxStemBytes = MAX_FILE_NAME_BYTES - extension.toByteArray(Charsets.UTF_8).size
        return truncateUtf8(stem, maxStemBytes) + extension
    }

    internal fun isHonorThemesDocumentId(documentId: String): Boolean {
        val parts = documentId.substringAfter(':', missingDelimiterValue = "")
            .split('/')
            .filter { it.isNotBlank() }
        return parts.size >= 2 && parts.last().equals("Themes", ignoreCase = true) &&
            parts[parts.lastIndex - 1].equals("Honor", ignoreCase = true)
    }

    private fun truncateUtf8(value: String, maxBytes: Int): String {
        var usedBytes = 0
        return buildString {
            var index = 0
            while (index < value.length) {
                val codePoint = value.codePointAt(index)
                val encoded = String(Character.toChars(codePoint)).toByteArray(Charsets.UTF_8)
                if (usedBytes + encoded.size > maxBytes) break
                append(String(Character.toChars(codePoint)))
                usedBytes += encoded.size
                index += Character.charCount(codePoint)
            }
        }
    }

    fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    fun validateHwt(file: File) {
        if (file.length() <= 0L) throw TransferException(422, "invalid_hwt", "HWT 文件为空")
        if (file.length() > MAX_FILE_SIZE) throw TransferException(413, "too_large", "HWT 文件超过 1 GiB 上限")
        try {
            ZipFile(file).use { archive ->
                val description = archive.getEntry("description.xml")
                if (description == null || description.isDirectory) {
                    throw TransferException(422, "invalid_hwt", "HWT 中缺少 description.xml")
                }
                val normalizedNames = HashSet<String>()
                val filePaths = HashSet<String>()
                val pathAncestors = HashSet<String>()
                val directoryPaths = HashSet<String>()
                val sizes = buildList {
                    val entries = archive.entries()
                    var entryCount = 0
                    while (entries.hasMoreElements()) {
                        val entry = entries.nextElement()
                        entryCount += 1
                        validateArchiveEntryCount(entryCount)
                        val normalizedName = normalizeArchivePath(entry.name)
                        if (!isSafeArchivePath(normalizedName)) {
                            throw TransferException(422, "invalid_hwt", "HWT ZIP 路径不安全")
                        }
                        if (!normalizedNames.add(normalizedName)) {
                            throw TransferException(422, "invalid_hwt", "HWT ZIP 存在规范化后的重复路径")
                        }
                        val topologyName = normalizedName.removeSuffix("/")
                        if (entry.isDirectory) {
                            if (topologyName in filePaths || hasFilePathParent(topologyName, filePaths)) {
                                throw TransferException(422, "invalid_hwt", "HWT ZIP 存在文件/目录路径重叠")
                            }
                            directoryPaths.add(topologyName)
                        } else {
                            if (topologyName in directoryPaths ||
                                topologyName in pathAncestors ||
                                hasFilePathParent(topologyName, filePaths)
                            ) {
                                throw TransferException(422, "invalid_hwt", "HWT ZIP 存在文件/目录路径重叠")
                            }
                            filePaths.add(topologyName)
                        }
                        addPathAncestors(topologyName, pathAncestors)
                        if (!entry.isDirectory) {
                            validateArchiveCompression(entry.size, entry.compressedSize)
                            add(entry.size)
                        }
                    }
                }
                validateArchiveBudget(sizes)
            }
        } catch (exc: TransferException) {
            throw exc
        } catch (exc: Exception) {
            throw TransferException(422, "invalid_hwt", "HWT ZIP 结构损坏")
        }
    }

    internal fun normalizeArchivePath(value: String): String =
        Normalizer.normalize(value, Normalizer.Form.NFC)

    private fun hasFilePathParent(path: String, filePaths: Set<String>): Boolean {
        var separator = path.indexOf('/')
        while (separator > 0) {
            if (path.substring(0, separator) in filePaths) return true
            separator = path.indexOf('/', separator + 1)
        }
        return false
    }

    private fun addPathAncestors(path: String, ancestors: MutableSet<String>) {
        var separator = path.indexOf('/')
        while (separator > 0) {
            ancestors.add(path.substring(0, separator))
            separator = path.indexOf('/', separator + 1)
        }
    }

    internal fun isSafeArchivePath(value: String): Boolean {
        if (value.isEmpty() || value.contains('\\') || value.contains(':') ||
            value.contains("\u0000") || value.startsWith('/')) {
            return false
        }
        val path = value.removeSuffix("/")
        if (path.isEmpty()) return false
        return path.split('/').all { part -> part.isNotEmpty() && part != "." && part != ".." }
    }

    internal fun validateArchiveCompression(size: Long, compressedSize: Long) {
        if (size < 0L || compressedSize < 0L || (size > 0L && compressedSize == 0L)) {
            throw TransferException(422, "invalid_hwt", "HWT ZIP 压缩大小无效")
        }
        if (size > 0L && size.toDouble() / compressedSize.toDouble() > MAX_ARCHIVE_COMPRESSION_RATIO) {
            throw TransferException(422, "invalid_hwt", "HWT ZIP 压缩比超过限制")
        }
    }

    internal fun validateArchiveBudget(sizes: Iterable<Long>) {
        var total = 0L
        var entryCount = 0
        for (size in sizes) {
            entryCount += 1
            validateArchiveEntryCount(entryCount)
            if (size < 0L || size > MAX_ARCHIVE_ENTRY_BYTES) {
                throw TransferException(422, "invalid_hwt", "HWT ZIP 条目大小无效")
            }
            if (size > MAX_ARCHIVE_UNCOMPRESSED_BYTES - total) {
                throw TransferException(422, "invalid_hwt", "HWT ZIP 解压总量超过限制")
            }
            total += size
        }
    }

    internal fun validateArchiveEntryCount(count: Int) {
        if (count < 0 || count > MAX_ARCHIVE_ENTRIES) {
            throw TransferException(422, "invalid_hwt", "HWT ZIP 条目数量超过限制")
        }
    }

    fun hasSufficientSpace(availableBytes: Long, fileSize: Long): Boolean {
        if (availableBytes < 0L || fileSize < 0L || fileSize > MAX_FILE_SIZE) return false
        return availableBytes >= fileSize + FREE_SPACE_RESERVE_BYTES
    }

    fun validateUploadLength(declaredSize: Long) {
        if (declaredSize < 0L) {
            throw TransferException(400, "invalid_length", "上传请求长度无效")
        }
        if (declaredSize > MAX_FILE_SIZE) {
            throw TransferException(413, "too_large", "HWT 文件超过 1 GiB 上限")
        }
    }

    fun shouldStopForIdle(nowMillis: Long, lastActivityMillis: Long, activeRequests: Int): Boolean {
        if (activeRequests != 0 || nowMillis < lastActivityMillis) return false
        return nowMillis - lastActivityMillis >= IDLE_TIMEOUT_MS
    }
}

class TransferException(
    val status: Int,
    val code: String,
    override val message: String,
) : Exception(message)

data class InstallResult(
    val storedName: String,
    val destination: String,
    val size: Long,
    val sha256: String,
    val overwritten: Boolean,
)
