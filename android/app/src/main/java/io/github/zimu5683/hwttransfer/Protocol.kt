package io.github.zimu5683.hwttransfer

import org.json.JSONException
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStream
import java.io.RandomAccessFile
import java.nio.file.Files
import java.security.MessageDigest
import java.text.Normalizer
import java.util.HashSet
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipFile

object Protocol {
    const val VERSION = 1
    const val DISCOVERY_PORT = 48620
    const val HTTP_PORT = 48621
    const val DISCOVERY_REQUEST = "HWTSTUDIO_DISCOVER_V1"
    const val FEATURE_DEVICE_PROFILE = "device_profile"
    const val FEATURE_TRANSFER_CANCEL = "transfer_cancel"
    const val FEATURE_TRANSFER_CHUNKED = "transfer_chunked"
    const val FEATURE_TRANSFER_PREPARE = "transfer_prepare"
    val ADVERTISED_FEATURES = listOf(
        FEATURE_DEVICE_PROFILE,
        FEATURE_TRANSFER_CANCEL,
        FEATURE_TRANSFER_CHUNKED,
        FEATURE_TRANSFER_PREPARE,
    )
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

    private class ArchivePathTracker {
        private val normalizedNames = HashSet<String>()
        private val filePaths = HashSet<String>()
        private val pathAncestors = HashSet<String>()
        private val directoryPaths = HashSet<String>()

        fun add(entry: ZipEntry) {
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
        }
    }

    private data class CentralDirectoryLocation(
        val offset: Long,
        val size: Long,
        val entryCount: Long,
    )

    private data class ArchiveDataSpan(
        val name: String,
        val start: Long,
        val end: Long,
    )

    fun parsePairRequest(raw: String): PairRequest {
        val body = try {
            JSONObject(raw)
        } catch (_: JSONException) {
            throw TransferException(400, "invalid_json", "配对请求 JSON 无效")
        }
        val code = body.opt("code")
        if (code !is String || !code.matches(Regex("[0-9]{6}"))) {
            throw TransferException(400, "invalid_request", "配对请求的 code 必须是 6 位数字")
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
                validateArchiveDataRanges(file)
                val description = archive.getEntry("description.xml")
                if (description == null || description.isDirectory) {
                    throw TransferException(422, "invalid_hwt", "HWT 中缺少 description.xml")
                }
                val paths = ArchivePathTracker()
                val sizes = buildList {
                    val entries = archive.entries()
                    var entryCount = 0
                    while (entries.hasMoreElements()) {
                        val entry = entries.nextElement()
                        entryCount += 1
                        validateArchiveEntryCount(entryCount)
                        paths.add(entry)
                        if (!entry.isDirectory) {
                            validateArchiveCompression(entry.size, entry.compressedSize)
                            add(entry.size)
                        }
                    }
                }
                validateArchiveBudget(sizes)
                val validationDirectory = Files.createTempDirectory(
                    (file.parentFile ?: File(".")).toPath(),
                    "hwt_validate_",
                ).toFile()
                try {
                    validateArchiveContents(archive, validationDirectory)
                } finally {
                    validationDirectory.deleteRecursively()
                }
            }
        } catch (exc: TransferException) {
            throw exc
        } catch (exc: Exception) {
            throw TransferException(422, "invalid_hwt", "HWT ZIP 结构损坏")
        }
    }

    /**
     * Validate the physical data ranges referenced by the central directory.
     * Java's ZipEntry does not expose local-header offsets, so this small
     * structural pass complements ZipFile's logical and CRC checks.
     */
    internal fun validateArchiveDataRanges(file: File) {
        try {
            RandomAccessFile(file, "r").use { archive ->
                val directory = readCentralDirectoryLocation(archive)
                if (directory.entryCount > MAX_ARCHIVE_ENTRIES) {
                    invalidArchiveData("中心目录条目数量超过限制")
                }
                val directoryEnd = addArchiveOffset(directory.offset, directory.size)
                if (directory.offset > archive.length() || directoryEnd > archive.length()) {
                    invalidArchiveData("中心目录超出文件范围")
                }
                val spans = ArrayList<ArchiveDataSpan>()
                var cursor = directory.offset
                repeat(directory.entryCount.toInt()) { index ->
                    val fixed = readArchiveBytes(archive, cursor, 46L)
                    if (readUnsignedInt(fixed, 0) != CENTRAL_DIRECTORY_SIGNATURE) {
                        invalidArchiveData("中心目录条目格式无效")
                    }
                    val filenameLength = readUnsignedShort(fixed, 28)
                    val extraLength = readUnsignedShort(fixed, 30)
                    val commentLength = readUnsignedShort(fixed, 32)
                    val recordSize = 46L + filenameLength + extraLength + commentLength
                    val recordEnd = addArchiveOffset(cursor, recordSize)
                    if (recordEnd > directoryEnd) {
                        invalidArchiveData("中心目录条目超出中心目录范围")
                    }
                    val filename = readArchiveBytes(archive, cursor + 46L, filenameLength.toLong())
                    val extra = readArchiveBytes(
                        archive,
                        cursor + 46L + filenameLength,
                        extraLength.toLong(),
                    )
                    var compressedSize = readUnsignedInt(fixed, 20)
                    var localHeaderOffset = readUnsignedInt(fixed, 42)
                    val zip64 = readZip64Values(
                        extra,
                        readUnsignedInt(fixed, 24) == ZIP32_SENTINEL,
                        compressedSize == ZIP32_SENTINEL,
                        localHeaderOffset == ZIP32_SENTINEL,
                    )
                    if (compressedSize == ZIP32_SENTINEL) {
                        compressedSize = zip64.compressedSize
                            ?: invalidArchiveData("条目 ${index + 1} 缺少 ZIP64 压缩大小")
                    }
                    if (localHeaderOffset == ZIP32_SENTINEL) {
                        localHeaderOffset = zip64.localHeaderOffset
                            ?: invalidArchiveData("条目 ${index + 1} 缺少 ZIP64 本地文件头偏移")
                    }
                    val localHeader = readArchiveBytes(archive, localHeaderOffset, LOCAL_FILE_HEADER_SIZE.toLong())
                    if (readUnsignedInt(localHeader, 0) != LOCAL_FILE_HEADER_SIGNATURE) {
                        invalidArchiveData("条目 ${index + 1} 的本地文件头无效")
                    }
                    val centralFlags = readUnsignedShort(fixed, 8)
                    val centralMethod = readUnsignedShort(fixed, 10)
                    val localFlags = readUnsignedShort(localHeader, 6)
                    val localMethod = readUnsignedShort(localHeader, 8)
                    if (centralFlags != localFlags || centralMethod != localMethod) {
                        invalidArchiveData("条目 ${index + 1} 的本地文件头属性与中心目录不一致")
                    }
                    val localFilenameLength = readUnsignedShort(localHeader, 26)
                    val localExtraLength = readUnsignedShort(localHeader, 28)
                    val localFilename = readArchiveBytes(
                        archive,
                        addArchiveOffset(localHeaderOffset, LOCAL_FILE_HEADER_SIZE),
                        localFilenameLength.toLong(),
                    )
                    if (!localFilename.contentEquals(filename)) {
                        invalidArchiveData("条目 ${index + 1} 的本地文件名与中心目录不一致")
                    }
                    val dataStart = addArchiveOffset(
                        localHeaderOffset,
                        LOCAL_FILE_HEADER_SIZE + localFilenameLength + localExtraLength,
                    )
                    val dataEnd = addArchiveOffset(dataStart, compressedSize)
                    if (dataEnd > directory.offset) {
                        invalidArchiveData("条目 ${index + 1} 的本地数据覆盖中心目录")
                    }
                    if (compressedSize > 0L) {
                        spans += ArchiveDataSpan(
                            name = decodeArchiveName(filename, index),
                            start = dataStart,
                            end = dataEnd,
                        )
                    }
                    cursor = recordEnd
                }
                if (cursor != directoryEnd) {
                    invalidArchiveData("中心目录长度与条目数量不一致")
                }
                var furthestEnd = -1L
                var furthestName = ""
                spans.sortedWith(compareBy<ArchiveDataSpan> { it.start }.thenBy { it.end }.thenBy { it.name })
                    .forEach { span ->
                        if (span.start < furthestEnd) {
                            invalidArchiveData("条目 ${span.name} 与 $furthestName 的本地数据区间重叠")
                        }
                        if (span.end > furthestEnd) {
                            furthestEnd = span.end
                            furthestName = span.name
                        }
                    }
            }
        } catch (exc: TransferException) {
            throw exc
        } catch (exc: Exception) {
            throw TransferException(422, "invalid_hwt", "HWT ZIP 本地数据区间校验失败").also {
                it.addSuppressed(exc)
            }
        }
    }

    private data class Zip64Values(
        val compressedSize: Long?,
        val localHeaderOffset: Long?,
    )

    private fun readCentralDirectoryLocation(archive: RandomAccessFile): CentralDirectoryLocation {
        val eocdOffset = findEndOfCentralDirectory(archive)
        if (eocdOffset < 0L) invalidArchiveData("缺少结束目录记录")
        val eocd = readArchiveBytes(archive, eocdOffset, END_OF_CENTRAL_DIRECTORY_SIZE.toLong())
        val diskNumber = readUnsignedShort(eocd, 4)
        val directoryDisk = readUnsignedShort(eocd, 6)
        val entriesOnDisk = readUnsignedShort(eocd, 8).toLong()
        val totalEntries = readUnsignedShort(eocd, 10).toLong()
        val directorySize = readUnsignedInt(eocd, 12)
        val directoryOffset = readUnsignedInt(eocd, 16)
        if (diskNumber != 0 || directoryDisk != 0) {
            invalidArchiveData("不支持跨磁盘 ZIP")
        }
        if (
            entriesOnDisk != ZIP16_SENTINEL.toLong() &&
            totalEntries != ZIP16_SENTINEL.toLong() &&
            directorySize != ZIP32_SENTINEL &&
            directoryOffset != ZIP32_SENTINEL
        ) {
            if (entriesOnDisk != totalEntries) invalidArchiveData("中心目录条目数量不一致")
            return CentralDirectoryLocation(directoryOffset, directorySize, totalEntries)
        }

        val locatorOffset = eocdOffset - ZIP64_LOCATOR_SIZE
        val locator = readArchiveBytes(archive, locatorOffset, ZIP64_LOCATOR_SIZE)
        if (
            readUnsignedInt(locator, 0) != ZIP64_LOCATOR_SIGNATURE ||
            readUnsignedInt(locator, 4) != 0L ||
            readUnsignedInt(locator, 16) != 1L
        ) {
            invalidArchiveData("ZIP64 结束目录定位记录无效")
        }
        val zip64Offset = readUnsignedLong(locator, 8)
        val zip64 = readArchiveBytes(archive, zip64Offset, ZIP64_END_OF_CENTRAL_DIRECTORY_SIZE)
        if (readUnsignedInt(zip64, 0) != ZIP64_END_OF_CENTRAL_DIRECTORY_SIGNATURE) {
            invalidArchiveData("ZIP64 结束目录记录无效")
        }
        val recordSize = readUnsignedLong(zip64, 4)
        if (recordSize < 44L || addArchiveOffset(zip64Offset, 12L + recordSize) > archive.length()) {
            invalidArchiveData("ZIP64 结束目录记录长度无效")
        }
        if (readUnsignedInt(zip64, 16) != 0L || readUnsignedInt(zip64, 20) != 0L) {
            invalidArchiveData("不支持跨磁盘 ZIP64")
        }
        return CentralDirectoryLocation(
            offset = readUnsignedLong(zip64, 48),
            size = readUnsignedLong(zip64, 40),
            entryCount = readUnsignedLong(zip64, 32),
        )
    }

    private fun findEndOfCentralDirectory(archive: RandomAccessFile): Long {
        val fileSize = archive.length()
        val searchStart = maxOf(0L, fileSize - END_OF_CENTRAL_DIRECTORY_SIZE - ZIP_COMMENT_MAX_SIZE)
        val window = readArchiveBytes(archive, searchStart, fileSize - searchStart)
        if (window.size < END_OF_CENTRAL_DIRECTORY_SIZE.toInt()) return -1L
        val lastCandidate = window.size - END_OF_CENTRAL_DIRECTORY_SIZE.toInt()
        for (index in lastCandidate downTo 0) {
            if (readUnsignedInt(window, index) != END_OF_CENTRAL_DIRECTORY_SIGNATURE) continue
            val commentLength = readUnsignedShort(window, index + 20)
            if (index + END_OF_CENTRAL_DIRECTORY_SIZE.toInt() + commentLength <= window.size) {
                return searchStart + index
            }
        }
        return -1L
    }

    private fun readZip64Values(
        extra: ByteArray,
        uncompressedSizeRequired: Boolean,
        compressedSizeRequired: Boolean,
        localHeaderOffsetRequired: Boolean,
    ): Zip64Values {
        if (!uncompressedSizeRequired && !compressedSizeRequired && !localHeaderOffsetRequired) {
            return Zip64Values(null, null)
        }
        var cursor = 0
        while (cursor + 4 <= extra.size) {
            val fieldSize = readUnsignedShort(extra, cursor + 2)
            val fieldStart = cursor + 4
            val fieldEnd = fieldStart + fieldSize
            if (fieldEnd > extra.size) invalidArchiveData("ZIP64 扩展字段长度无效")
            if (readUnsignedShort(extra, cursor) == ZIP64_EXTRA_FIELD_ID) {
                var valueCursor = fieldStart
                // The ZIP64 extra field contains only values whose 32-bit
                // central-directory fields use the sentinel value.
                if (uncompressedSizeRequired) {
                    if (valueCursor + 8 > fieldEnd) invalidArchiveData("ZIP64 扩展字段缺少解压大小")
                    valueCursor += 8
                }
                val compressedSize = if (compressedSizeRequired) {
                    if (valueCursor + 8 > fieldEnd) invalidArchiveData("ZIP64 扩展字段缺少压缩大小")
                    readUnsignedLong(extra, valueCursor).also { valueCursor += 8 }
                } else {
                    null
                }
                val localHeaderOffset = if (localHeaderOffsetRequired) {
                    if (valueCursor + 8 > fieldEnd) invalidArchiveData("ZIP64 扩展字段缺少本地偏移")
                    readUnsignedLong(extra, valueCursor)
                } else {
                    null
                }
                return Zip64Values(compressedSize, localHeaderOffset)
            }
            cursor = fieldEnd
        }
        return Zip64Values(null, null)
    }

    private fun readArchiveBytes(archive: RandomAccessFile, offset: Long, size: Long): ByteArray {
        if (offset < 0L || size < 0L || size > Int.MAX_VALUE || offset > archive.length() - size) {
            invalidArchiveData("ZIP 结构超出文件范围")
        }
        val result = ByteArray(size.toInt())
        archive.seek(offset)
        archive.readFully(result)
        return result
    }

    private fun addArchiveOffset(left: Long, right: Long): Long {
        if (left < 0L || right < 0L || left > Long.MAX_VALUE - right) {
            invalidArchiveData("ZIP 偏移量溢出")
        }
        return left + right
    }

    private fun decodeArchiveName(raw: ByteArray, index: Int): String =
        raw.toString(Charsets.UTF_8).takeIf { it.isNotBlank() } ?: "entry-${index + 1}"

    private fun readUnsignedShort(raw: ByteArray, offset: Int): Int =
        (raw[offset].toInt() and 0xff) or ((raw[offset + 1].toInt() and 0xff) shl 8)

    private fun readUnsignedInt(raw: ByteArray, offset: Int): Long =
        (raw[offset].toLong() and 0xffL) or
            ((raw[offset + 1].toLong() and 0xffL) shl 8) or
            ((raw[offset + 2].toLong() and 0xffL) shl 16) or
            ((raw[offset + 3].toLong() and 0xffL) shl 24)

    private fun readUnsignedLong(raw: ByteArray, offset: Int): Long {
        var result = 0L
        for (index in 7 downTo 0) {
            val value = raw[offset + index].toLong() and 0xffL
            if (result > (Long.MAX_VALUE - value) / 256L) {
                invalidArchiveData("ZIP64 偏移量超出支持范围")
            }
            result = result * 256L + value
        }
        return result
    }

    private fun invalidArchiveData(message: String): Nothing =
        throw TransferException(422, "invalid_hwt", "HWT ZIP 本地数据区间无效：$message")

    private fun validateArchiveContents(archive: ZipFile, validationDirectory: File) {
        val buffer = ByteArray(1024 * 1024)
        var totalBytes = 0L
        try {
            val entries = archive.entries()
            while (entries.hasMoreElements()) {
                val entry = entries.nextElement()
                if (entry.isDirectory) continue
                BufferedInputStream(archive.getInputStream(entry)).use { input ->
                    val nestedBytes: Long
                    val entryBytes: Long
                    if (startsLikeZip(input)) {
                        val nestedFile = File.createTempFile("hwt_nested_", ".zip", validationDirectory)
                        try {
                            entryBytes = materializeNestedArchive(
                                input,
                                nestedFile,
                                totalBytes,
                                entry.size,
                                entry.crc,
                            )
                            validateArchiveDataRanges(nestedFile)
                            nestedBytes = validateNestedArchive(nestedFile, totalBytes + entryBytes)
                        } finally {
                            nestedFile.delete()
                        }
                    } else {
                        nestedBytes = 0L
                        entryBytes = drainArchiveEntry(input, buffer, totalBytes, entry.crc)
                    }
                    if (entryBytes > MAX_ARCHIVE_ENTRY_BYTES) {
                        throw TransferException(422, "invalid_hwt", "HWT ZIP 解压总量超过限制")
                    }
                    validateArchiveExpansionBudget(totalBytes, entryBytes + nestedBytes)
                    totalBytes += entryBytes + nestedBytes
                }
            }
        } catch (exc: TransferException) {
            throw exc
        } catch (exc: Exception) {
            throw TransferException(422, "invalid_hwt", "HWT ZIP 内容校验失败").also {
                it.addSuppressed(exc)
            }
        }
    }

    private fun materializeNestedArchive(
        input: InputStream,
        target: File,
        previousTotal: Long,
        expectedSize: Long,
        expectedCrc: Long,
    ): Long {
        val buffer = ByteArray(1024 * 1024)
        val checksum = CRC32()
        var entryBytes = 0L
        FileOutputStream(target).use { output ->
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                entryBytes += count
                if (entryBytes > MAX_ARCHIVE_ENTRY_BYTES) {
                    throw TransferException(422, "invalid_hwt", "HWT ZIP 解压总量超过限制")
                }
                validateArchiveExpansionBudget(previousTotal, entryBytes)
                checksum.update(buffer, 0, count)
                output.write(buffer, 0, count)
            }
        }
        if (expectedSize >= 0L && expectedSize != entryBytes) {
            throw TransferException(422, "invalid_hwt", "HWT 嵌套 ZIP 条目大小无效")
        }
        if (expectedCrc >= 0L && checksum.value != expectedCrc) {
            throw TransferException(422, "invalid_hwt", "HWT ZIP CRC 校验失败")
        }
        return entryBytes
    }

    private fun startsLikeZip(input: BufferedInputStream): Boolean {
        input.mark(4)
        val signature = ByteArray(4)
        var offset = 0
        while (offset < signature.size) {
            val count = input.read(signature, offset, signature.size - offset)
            if (count < 0) break
            if (count == 0) continue
            offset += count
        }
        input.reset()
        if (offset != signature.size) return false
        return signature.contentEquals(byteArrayOf(0x50, 0x4b, 0x03, 0x04)) ||
            signature.contentEquals(byteArrayOf(0x50, 0x4b, 0x05, 0x06)) ||
            signature.contentEquals(byteArrayOf(0x50, 0x4b, 0x07, 0x08))
    }

    private fun drainArchiveEntry(
        input: InputStream,
        buffer: ByteArray,
        previousTotal: Long,
        expectedCrc: Long,
    ): Long {
        val checksum = CRC32()
        var entryBytes = 0L
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            entryBytes += count
            if (entryBytes > MAX_ARCHIVE_ENTRY_BYTES) {
                throw TransferException(422, "invalid_hwt", "HWT ZIP 解压总量超过限制")
            }
            checksum.update(buffer, 0, count)
            validateArchiveExpansionBudget(previousTotal, entryBytes)
        }
        if (expectedCrc >= 0L && checksum.value != expectedCrc) {
            throw TransferException(422, "invalid_hwt", "HWT ZIP CRC 校验失败")
        }
        return entryBytes
    }

    private fun validateNestedArchive(file: File, previousTotal: Long): Long {
        val buffer = ByteArray(1024 * 1024)
        val paths = ArchivePathTracker()
        var totalBytes = 0L
        var entryCount = 0
        try {
            ZipInputStream(FileInputStream(file)).use { archive ->
                while (true) {
                    val entry = archive.nextEntry ?: break
                    entryCount += 1
                    validateArchiveEntryCount(entryCount)
                    paths.add(entry)
                    if (entry.isDirectory) {
                        archive.closeEntry()
                        continue
                    }
                    if (entry.size > MAX_ARCHIVE_ENTRY_BYTES) {
                        throw TransferException(422, "invalid_hwt", "HWT 嵌套 ZIP 解压总量超过限制")
                    }
                    if (entry.size >= 0L) {
                        validateArchiveExpansionBudget(previousTotal + totalBytes, entry.size)
                    }
                    val expectedCrc = entry.crc
                    val checksum = CRC32()
                    var entryBytes = 0L
                    while (true) {
                        val count = archive.read(buffer)
                        if (count < 0) break
                        entryBytes += count
                        checksum.update(buffer, 0, count)
                        if (entryBytes > MAX_ARCHIVE_ENTRY_BYTES) {
                            throw TransferException(422, "invalid_hwt", "HWT 嵌套 ZIP 解压总量超过限制")
                        }
                        validateArchiveExpansionBudget(previousTotal + totalBytes, entryBytes)
                    }
                    archive.closeEntry()
                    if (entry.size >= 0L && entry.size != entryBytes) {
                        throw TransferException(422, "invalid_hwt", "HWT 嵌套 ZIP 条目大小无效")
                    }
                    val actualExpectedCrc = if (entry.crc >= 0L) entry.crc else expectedCrc
                    if (actualExpectedCrc >= 0L && checksum.value != actualExpectedCrc) {
                        throw TransferException(422, "invalid_hwt", "HWT 嵌套 ZIP CRC 校验失败")
                    }
                    if (entry.compressedSize >= 0L) {
                        validateArchiveCompression(entryBytes, entry.compressedSize)
                    }
                    totalBytes += entryBytes
                }
            }
            return totalBytes
        } catch (exc: TransferException) {
            throw exc
        } catch (exc: Exception) {
            throw TransferException(422, "invalid_hwt", "HWT 嵌套 ZIP 内容校验失败").also {
                it.addSuppressed(exc)
            }
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

    internal fun validateArchiveExpansionBudget(usedBytes: Long, additionalBytes: Long) {
        if (usedBytes < 0L || additionalBytes < 0L ||
            additionalBytes > MAX_ARCHIVE_UNCOMPRESSED_BYTES - usedBytes
        ) {
            throw TransferException(422, "invalid_hwt", "HWT ZIP 解压总量超过限制")
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
            validateArchiveExpansionBudget(total, size)
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

    private const val LOCAL_FILE_HEADER_SIGNATURE = 0x04034b50L
    private const val CENTRAL_DIRECTORY_SIGNATURE = 0x02014b50L
    private const val END_OF_CENTRAL_DIRECTORY_SIGNATURE = 0x06054b50L
    private const val ZIP64_LOCATOR_SIGNATURE = 0x07064b50L
    private const val ZIP64_END_OF_CENTRAL_DIRECTORY_SIGNATURE = 0x06064b50L
    private const val ZIP64_EXTRA_FIELD_ID = 0x0001
    private const val LOCAL_FILE_HEADER_SIZE = 30L
    private const val END_OF_CENTRAL_DIRECTORY_SIZE = 22L
    private const val ZIP64_LOCATOR_SIZE = 20L
    private const val ZIP64_END_OF_CENTRAL_DIRECTORY_SIZE = 56L
    private const val ZIP_COMMENT_MAX_SIZE = 0xffffL
    private const val ZIP16_SENTINEL = 0xffff
    private const val ZIP32_SENTINEL = 0xffffffffL
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
