package io.github.zimu5683.hwttransfer

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class ProtocolTest {
    @Test
    fun profilePackageListIsThemeSpecific() {
        assertTrue(DeviceProfile.targetPackages.contains("com.android.systemui"))
        assertTrue(DeviceProfile.targetPackages.contains("com.hihonor.android.launcher"))
        assertTrue(DeviceProfile.targetPackages.contains("com.tencent.mm"))
        assertTrue(DeviceProfile.targetPackages.none { it == "*" })
    }

    @Test
    fun safeFileNameRemovesTraversalAndKeepsChinese() {
        assertEquals("我的_主题.hwt", Protocol.safeFileName("../我的 主题.hwt"))
        assertEquals("theme.hwt", Protocol.safeFileName("theme"))
    }

    @Test
    fun safeFileNameIsBoundedInUtf8Bytes() {
        val name = Protocol.safeFileName("主题".repeat(200))

        assertTrue(name.endsWith(".hwt"))
        assertTrue(name.toByteArray(Charsets.UTF_8).size <= Protocol.MAX_FILE_NAME_BYTES)
    }

    @Test
    fun honorThemesDocumentIdRequiresHonorParent() {
        assertTrue(Protocol.isHonorThemesDocumentId("primary:Honor/Themes"))
        assertTrue(Protocol.isHonorThemesDocumentId("ABCD-1234:Honor/Themes/"))
        assertFalse(Protocol.isHonorThemesDocumentId("primary:Download/Themes"))
        assertFalse(Protocol.isHonorThemesDocumentId("primary:Honor/Archive/Themes"))
        assertFalse(Protocol.isHonorThemesDocumentId("Themes"))
    }

    @Test
    fun pairingStateKeepsValidEntriesWhenOneEntryIsCorrupt() {
        val validHash = "a".repeat(64)
        val raw = JSONArray()
            .put(JSONObject().put("name", "编辑器").put("token_hash", validHash).put("paired_at", 123L))
            .put(JSONObject().put("name", "损坏记录").put("token_hash", "not-a-hash"))
            .toString()

        assertEquals(
            listOf(PairedClient("编辑器", validHash, 123L)),
            PairingManager.parseClients(raw),
        )
    }

    @Test
    fun pairingStateRejectsWrongFieldTypesAndInvalidTimes() {
        val validHash = "1".repeat(64)
        val raw = JSONArray()
            .put(JSONObject().put("name", 7).put("token_hash", validHash).put("paired_at", 123L))
            .put(JSONObject().put("name", "字符串时间").put("token_hash", validHash).put("paired_at", "123"))
            .put(JSONObject().put("name", "负时间").put("token_hash", validHash).put("paired_at", -1L))
            .put(JSONObject().put("name", "小数时间").put("token_hash", validHash).put("paired_at", 1.5))

        assertTrue(PairingManager.parseClients(raw.toString()).isEmpty())
    }

    @Test
    fun pairingStateRejectsUnsafeOrOversizedClientNames() {
        val validHash = "2".repeat(64)
        val raw = JSONArray()
            .put(JSONObject().put("name", "带\n换行").put("token_hash", validHash).put("paired_at", 123L))
            .put(JSONObject().put("name", "x".repeat(Protocol.MAX_CLIENT_NAME_CODE_POINTS + 1)).put("token_hash", validHash).put("paired_at", 123L))

        assertTrue(PairingManager.parseClients(raw.toString()).isEmpty())
    }

    @Test
    fun authorizationTokenInputIsBoundBeforeHashing() {
        assertFalse(isAcceptableAuthorizationToken(null))
        assertFalse(isAcceptableAuthorizationToken(" "))
        assertTrue(isAcceptableAuthorizationToken("a".repeat(43)))
        assertFalse(isAcceptableAuthorizationToken("a".repeat(MAX_AUTH_TOKEN_CHARS + 1)))
    }

    @Test
    fun pairRequestRejectsMalformedJsonAndWrongFieldTypes() {
        val malformed = assertThrows(TransferException::class.java) {
            Protocol.parsePairRequest("{")
        }
        assertEquals("invalid_json", malformed.code)

        val wrongCodeType = assertThrows(TransferException::class.java) {
            Protocol.parsePairRequest("{\"code\":123456}")
        }
        assertEquals("invalid_request", wrongCodeType.code)

        val wrongNameType = assertThrows(TransferException::class.java) {
            Protocol.parsePairRequest("{\"code\":\"123456\",\"client_name\":7}")
        }
        assertEquals("invalid_request", wrongNameType.code)
    }

    @Test
    fun pairRequestRejectsInvalidCodeFormatBeforePairing() {
        listOf("12345", "1234567", "12a456").forEach { code ->
            val error = assertThrows(TransferException::class.java) {
                Protocol.parsePairRequest("{\"code\":\"$code\"}")
            }
            assertEquals("invalid_request", error.code)
        }
    }

    @Test
    fun pairRequestKeepsOptionalNameDefault() {
        assertEquals(
            Protocol.PairRequest("123456", ""),
            Protocol.parsePairRequest("{\"code\":\"123456\"}"),
        )
    }

    @Test
    fun freeSpaceCheckIncludesReserveAndFailsClosed() {
        assertTrue(Protocol.hasSufficientSpace(
            Protocol.FREE_SPACE_RESERVE_BYTES + 1024L,
            1024L,
        ))
        assertTrue(Protocol.hasSufficientSpace(
            Protocol.FREE_SPACE_RESERVE_BYTES + Protocol.MAX_FILE_SIZE,
            Protocol.MAX_FILE_SIZE,
        ))
        assertTrue(!Protocol.hasSufficientSpace(0L, 0L))
        assertTrue(!Protocol.hasSufficientSpace(Protocol.MAX_FILE_SIZE, Protocol.MAX_FILE_SIZE))
        assertTrue(!Protocol.hasSufficientSpace(Long.MAX_VALUE, Protocol.MAX_FILE_SIZE + 1L))
    }

    @Test
    fun idleStopRequiresExactBoundaryAndNoActiveRequest() {
        val start = 1_000L
        assertFalse(Protocol.shouldStopForIdle(start + Protocol.IDLE_TIMEOUT_MS - 1L, start, 0))
        assertTrue(Protocol.shouldStopForIdle(start + Protocol.IDLE_TIMEOUT_MS, start, 0))
        assertFalse(Protocol.shouldStopForIdle(start + Protocol.IDLE_TIMEOUT_MS, start, 1))
        assertFalse(Protocol.shouldStopForIdle(start - 1L, start, 0))
    }

    @Test
    fun uploadLengthDistinguishesInvalidAndOversizedValues() {
        val negative = assertThrows(TransferException::class.java) {
            Protocol.validateUploadLength(-1L)
        }
        assertEquals(400, negative.status)
        assertEquals("invalid_length", negative.code)

        val oversized = assertThrows(TransferException::class.java) {
            Protocol.validateUploadLength(Protocol.MAX_FILE_SIZE + 1L)
        }
        assertEquals(413, oversized.status)
        assertEquals("too_large", oversized.code)
    }

    @Test
    fun transferCancelFeatureUsesStableProtocolIdentifier() {
        assertEquals("transfer_cancel", Protocol.FEATURE_TRANSFER_CANCEL)
    }

    @Test
    fun advertisedFeaturesAreCentralizedForEveryHandshake() {
        assertEquals(
            listOf(
                Protocol.FEATURE_DEVICE_PROFILE,
                Protocol.FEATURE_TRANSFER_CANCEL,
                Protocol.FEATURE_TRANSFER_CHUNKED,
                Protocol.FEATURE_TRANSFER_PREPARE,
            ),
            Protocol.ADVERTISED_FEATURES,
        )
    }

    @Test
    fun chunkedTransferFeatureAndBudgetAreStable() {
        assertEquals("transfer_chunked", Protocol.FEATURE_TRANSFER_CHUNKED)
        assertEquals("transfer_prepare", Protocol.FEATURE_TRANSFER_PREPARE)
        assertTrue(Protocol.MAX_TRANSFER_CHUNK_BYTES <= 16L * 1024L * 1024L)
        assertTrue(Protocol.MAX_TRANSFER_CHUNK_BYTES > 0L)
    }

    @Test
    fun transferPrepareParsesAndNormalizesMetadata() {
        val request = Protocol.parseTransferPrepare(
            "{\"file_name\":\"../主题.hwt\",\"size\":12,\"sha256\":\"${"a".repeat(64)}\"}",
        )

        assertEquals("主题.hwt", request.fileName)
        assertEquals(12L, request.totalSize)
        assertEquals("a".repeat(64), request.sha256)
    }

    @Test
    fun transferPrepareRejectsWrongMetadataTypes() {
        val error = assertThrows(TransferException::class.java) {
            Protocol.parseTransferPrepare(
                "{\"file_name\":\"theme.hwt\",\"size\":\"12\",\"sha256\":\"${"a".repeat(64)}\"}",
            )
        }
        assertEquals("invalid_request", error.code)
    }

    @Test
    fun validateHwtRequiresDescription() {
        val file = File.createTempFile("invalid", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("wallpaper/test.jpg"))
                zip.write(byteArrayOf(1, 2, 3))
                zip.closeEntry()
            }
            assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtAcceptsThemeSkeleton() {
        val file = File.createTempFile("valid", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
            }
            Protocol.validateHwt(file)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtAcceptsSequentialZipDataRanges() {
        val file = File.createTempFile("sequential", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("second.bin"))
                zip.write("normal payload".toByteArray())
                zip.closeEntry()
            }
            Protocol.validateHwt(file)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsCrcCorruption() {
        val file = File.createTempFile("crc", ".hwt")
        val content = byteArrayOf(0x11, 0x22, 0x33, 0x44, 0x55)
        try {
            val entry = ZipEntry("description.xml").apply {
                method = ZipEntry.STORED
                size = content.size.toLong()
                crc = CRC32().apply { update(content) }.value
            }
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(entry)
                zip.write(content)
                zip.closeEntry()
            }
            val encoded = file.readBytes()
            val fileNameBytes = "description.xml".toByteArray(Charsets.UTF_8)
            val localHeaderSize = 30
            val payloadOffset = localHeaderSize + fileNameBytes.size
            assertEquals(0x50, encoded[0].toInt() and 0xff)
            assertEquals(0x4b, encoded[1].toInt() and 0xff)
            assertEquals(content.toList(), encoded.slice(payloadOffset until payloadOffset + content.size))
            encoded[payloadOffset] = (encoded[payloadOffset].toInt() xor 0x01).toByte()
            file.writeBytes(encoded)

            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtStructureSkipsDecompressionAndContentsCatchesCorruption() {
        val file = File.createTempFile("split-validation", ".hwt")
        val content = byteArrayOf(0x11, 0x22, 0x33, 0x44, 0x55)
        try {
            val entry = ZipEntry("description.xml").apply {
                method = ZipEntry.STORED
                size = content.size.toLong()
                crc = CRC32().apply { update(content) }.value
            }
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(entry)
                zip.write(content)
                zip.closeEntry()
            }
            val encoded = file.readBytes()
            val fileNameBytes = "description.xml".toByteArray(Charsets.UTF_8)
            val payloadOffset = 30 + fileNameBytes.size
            encoded[payloadOffset] = (encoded[payloadOffset].toInt() xor 0x01).toByte()
            file.writeBytes(encoded)

            // Fast path only inspects the central directory, so CRC corruption
            // in the payload is deferred to the background contents check.
            Protocol.validateHwtStructure(file)

            val error = assertThrows(TransferException::class.java) { Protocol.validateHwtContents(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtStructureStillRequiresDescription() {
        val file = File.createTempFile("structure-missing-description", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("wallpaper/test.jpg"))
                zip.write(byteArrayOf(1, 2, 3))
                zip.closeEntry()
            }
            val error = assertThrows(TransferException::class.java) { Protocol.validateHwtStructure(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtContentsAcceptsValidSkeleton() {
        val file = File.createTempFile("contents-valid", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("preview/cover.jpg"))
                zip.write(byteArrayOf(1, 2, 3))
                zip.closeEntry()
            }
            Protocol.validateHwtContents(file)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsPhysicallyOverlappingZipData() {
        val file = File.createTempFile("data-overlap", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("second.bin"))
                zip.write("same payload".toByteArray())
                zip.closeEntry()
            }
            val encoded = file.readBytes()
            val firstCentral = encoded.indexOfSignature(byteArrayOf(0x50, 0x4b, 0x01, 0x02))
            val secondCentral = encoded.indexOfSignature(
                byteArrayOf(0x50, 0x4b, 0x01, 0x02),
                firstCentral + 4,
            )
            assertTrue(firstCentral >= 0)
            assertTrue(secondCentral > firstCentral)
            val firstOffset = ByteBuffer.wrap(encoded, firstCentral + 42, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .int
            ByteBuffer.wrap(encoded, secondCentral + 42, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .putInt(firstOffset)
            file.writeBytes(encoded)

            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsCentralDirectoryLocalHeaderNameMismatch() {
        val file = File.createTempFile("local-header-mismatch", ".hwt")
        val payload = "same payload".toByteArray()
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write(payload)
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("second.bin"))
                zip.write(payload)
                zip.closeEntry()
            }
            val encoded = file.readBytes()
            val firstCentral = encoded.indexOfSignature(byteArrayOf(0x50, 0x4b, 0x01, 0x02))
            val secondCentral = encoded.indexOfSignature(
                byteArrayOf(0x50, 0x4b, 0x01, 0x02),
                firstCentral + 4,
            )
            assertTrue(firstCentral >= 0)
            assertTrue(secondCentral > firstCentral)
            val firstOffset = ByteBuffer.wrap(encoded, firstCentral + 42, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .int
            val secondOffset = ByteBuffer.wrap(encoded, secondCentral + 42, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .int
            ByteBuffer.wrap(encoded, firstCentral + 42, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .putInt(secondOffset)
            ByteBuffer.wrap(encoded, secondCentral + 42, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .putInt(firstOffset)
            file.writeBytes(encoded)

            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsLocalHeaderMetadataMismatch() {
        val file = File.createTempFile("local-header-metadata-mismatch", ".hwt")
        val content = "<HwTheme/>".toByteArray()
        try {
            val entry = ZipEntry("description.xml").apply {
                method = ZipEntry.STORED
                size = content.size.toLong()
                crc = CRC32().apply { update(content) }.value
            }
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(entry)
                zip.write(content)
                zip.closeEntry()
            }
            val encoded = file.readBytes()
            val localCrc = ByteBuffer.wrap(encoded, 14, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .int
            ByteBuffer.wrap(encoded, 14, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .putInt(localCrc xor 1)
            file.writeBytes(encoded)

            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsNestedCrcCorruption() {
        val file = File.createTempFile("nested-crc", ".hwt")
        val content = byteArrayOf(0x21, 0x32, 0x43, 0x54)
        try {
            val nestedBytes = ByteArrayOutputStream()
            val entry = ZipEntry("theme.xml").apply {
                method = ZipEntry.STORED
                size = content.size.toLong()
                crc = CRC32().apply { update(content) }.value
            }
            ZipOutputStream(nestedBytes).use { zip ->
                zip.putNextEntry(entry)
                zip.write(content)
                zip.closeEntry()
            }
            val nested = nestedBytes.toByteArray()
            val payloadOffset = 30 + "theme.xml".toByteArray(Charsets.UTF_8).size
            nested[payloadOffset] = (nested[payloadOffset].toInt() xor 0x01).toByte()

            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("icons"))
                zip.write(nested)
                zip.closeEntry()
            }

            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsPhysicallyOverlappingNestedZipData() {
        val file = File.createTempFile("nested-data-overlap", ".hwt")
        try {
            val nestedBytes = ByteArrayOutputStream()
            ZipOutputStream(nestedBytes).use { zip ->
                zip.putNextEntry(ZipEntry("theme.xml"))
                zip.write("<resources/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("preview.png"))
                zip.write("same payload".toByteArray())
                zip.closeEntry()
            }
            val encoded = nestedBytes.toByteArray()
            val firstCentral = encoded.indexOfSignature(byteArrayOf(0x50, 0x4b, 0x01, 0x02))
            val secondCentral = encoded.indexOfSignature(
                byteArrayOf(0x50, 0x4b, 0x01, 0x02),
                firstCentral + 4,
            )
            assertTrue(firstCentral >= 0)
            assertTrue(secondCentral > firstCentral)
            val firstOffset = ByteBuffer.wrap(encoded, firstCentral + 42, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .int
            ByteBuffer.wrap(encoded, secondCentral + 42, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .putInt(firstOffset)

            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("icons"))
                zip.write(encoded)
                zip.closeEntry()
            }

            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsNestedUnixSymlinkEntry() {
        val file = File.createTempFile("nested-symlink", ".hwt")
        try {
            val nestedBytes = ByteArrayOutputStream()
            ZipOutputStream(nestedBytes).use { zip ->
                zip.putNextEntry(ZipEntry("link"))
                zip.write("../outside.txt".toByteArray())
                zip.closeEntry()
            }
            val encoded = nestedBytes.toByteArray()
            val central = encoded.indexOfSignature(byteArrayOf(0x50, 0x4b, 0x01, 0x02))
            assertTrue(central >= 0)
            ByteBuffer.wrap(encoded, central + 4, 2)
                .order(ByteOrder.LITTLE_ENDIAN)
                .putShort(((3 shl 8) or 20).toShort())
            ByteBuffer.wrap(encoded, central + 38, 4)
                .order(ByteOrder.LITTLE_ENDIAN)
                .putInt(Integer.MIN_VALUE or 0x20000000)

            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("icons"))
                zip.write(encoded)
                zip.closeEntry()
            }

            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsUnsafeNestedPath() {
        val file = File.createTempFile("nested-path", ".hwt")
        try {
            val nestedBytes = ByteArrayOutputStream()
            ZipOutputStream(nestedBytes).use { zip ->
                zip.putNextEntry(ZipEntry("../escape.xml"))
                zip.write("<resources/>".toByteArray())
                zip.closeEntry()
            }
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("icons"))
                zip.write(nestedBytes.toByteArray())
                zip.closeEntry()
            }

            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsUnsafeAndNormalizedDuplicatePaths() {
        val file = File.createTempFile("unsafe", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("e\u0301.txt"))
                zip.write(byteArrayOf(1))
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("\u00e9.txt"))
                zip.write(byteArrayOf(2))
                zip.closeEntry()
            }
            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsFileDirectoryPathOverlap() {
        val file = File.createTempFile("overlap", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("icons/theme.png"))
                zip.write(byteArrayOf(2))
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("icons"))
                zip.write(byteArrayOf(1))
                zip.closeEntry()
            }
            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsFileParentOfDirectoryEntry() {
        val file = File.createTempFile("directory-overlap", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("icons/assets/"))
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("icons"))
                zip.write(byteArrayOf(1))
                zip.closeEntry()
            }
            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun validateHwtRejectsHighCompressionRatioBeforeReadingEntry() {
        val file = File.createTempFile("bomb", ".hwt")
        try {
            ZipOutputStream(file.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("description.xml"))
                zip.write("<HwTheme/>".toByteArray())
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("bomb.bin"))
                zip.write(ByteArray(2 * 1024 * 1024))
                zip.closeEntry()
            }
            val error = assertThrows(TransferException::class.java) { Protocol.validateHwt(file) }
            assertEquals("invalid_hwt", error.code)
        } finally {
            file.delete()
        }
    }

    @Test
    fun archivePathAndCompressionHelpersKeepSafeBoundaries() {
        assertTrue(Protocol.isSafeArchivePath("preview/cover.jpg"))
        assertTrue(!Protocol.isSafeArchivePath("../escape.jpg"))
        assertEquals("\u00e9.txt", Protocol.normalizeArchivePath("e\u0301.txt"))
        Protocol.validateArchiveCompression(500L, 1L)
        assertThrows(TransferException::class.java) {
            Protocol.validateArchiveCompression(501L, 1L)
        }
    }

    @Test
    fun archiveBudgetRejectsOversizedEntryBeforeInstall() {
        val error = assertThrows(TransferException::class.java) {
            Protocol.validateArchiveBudget(listOf(Protocol.MAX_ARCHIVE_ENTRY_BYTES + 1L))
        }
        assertEquals("invalid_hwt", error.code)
    }

    @Test
    fun archiveBudgetRejectsCumulativeExpansion() {
        val error = assertThrows(TransferException::class.java) {
            Protocol.validateArchiveBudget(
                listOf(Protocol.MAX_ARCHIVE_ENTRY_BYTES, Protocol.MAX_ARCHIVE_ENTRY_BYTES, Protocol.MAX_ARCHIVE_ENTRY_BYTES),
            )
        }
        assertEquals("invalid_hwt", error.code)
    }

    @Test
    fun archiveExpansionBudgetIncludesNestedBytesInOuterTotal() {
        Protocol.validateArchiveExpansionBudget(
            Protocol.MAX_ARCHIVE_UNCOMPRESSED_BYTES - 128L,
            128L,
        )
        val error = assertThrows(TransferException::class.java) {
            Protocol.validateArchiveExpansionBudget(
                Protocol.MAX_ARCHIVE_UNCOMPRESSED_BYTES - 128L,
                129L,
            )
        }
        assertEquals("invalid_hwt", error.code)
    }

    @Test
    fun archiveEntryCountRejectsExcessiveCentralDirectory() {
        val error = assertThrows(TransferException::class.java) {
            Protocol.validateArchiveEntryCount(Protocol.MAX_ARCHIVE_ENTRIES + 1)
        }
        assertEquals("invalid_hwt", error.code)
    }

    private fun ByteArray.indexOfSignature(signature: ByteArray, start: Int = 0): Int {
        for (index in start..(size - signature.size)) {
            if (copyOfRange(index, index + signature.size).contentEquals(signature)) return index
        }
        return -1
    }
}
