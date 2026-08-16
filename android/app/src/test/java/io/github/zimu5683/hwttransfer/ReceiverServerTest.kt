package io.github.zimu5683.hwttransfer

import java.io.ByteArrayInputStream
import java.io.File
import java.nio.file.Files
import java.nio.file.LinkOption
import java.security.MessageDigest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReceiverServerTest {
    @Test
    fun cachedTransferRequiresTheSameNormalizedFileIdentity() {
        val cached = InstallResult(
            storedName = "原主题.hwt",
            destination = "Honor/Themes/原主题.hwt",
            size = 7L,
            sha256 = "a".repeat(64),
            overwritten = false,
        )

        validateCachedTransfer(cached, "原主题.hwt", 7L, "A".repeat(64))
        val error = org.junit.Assert.assertThrows(TransferException::class.java) {
            validateCachedTransfer(cached, "另一主题.hwt", 7L, "a".repeat(64))
        }
        assertEquals(409, error.status)
        assertEquals("transfer_id_reused", error.code)
    }

    @Test
    fun chunkCommitRequiresAnEmptyRequestBody() {
        validateEmptyRequestLength(0L)
        val error = org.junit.Assert.assertThrows(TransferException::class.java) {
            validateEmptyRequestLength(1L)
        }
        assertEquals("invalid_body", error.code)
    }

    @Test
    fun parsedUploadCleanupDeletesOnlyTheParsedFile() {
        val root = Files.createTempDirectory("hwt-parsed-upload-cleanup-test")
        try {
            val parsed = root.resolve("parsed-upload.tmp").toFile()
            parsed.writeText("payload")

            assertTrue(deleteParsedUploadFile(parsed))
            assertFalse(parsed.exists())
            assertTrue(deleteParsedUploadFile(parsed))
        } finally {
            root.toFile().deleteRecursively()
        }
    }

    @Test
    fun staleChunkUploadFilesOnlyReturnsRegularFilesWithValidSessionNames() {
        val root = Files.createTempDirectory("hwt-chunk-cleanup-test")
        try {
            val stale = root.resolve("hwt_chunk_${"a".repeat(32)}.uploading").toFile()
            stale.writeText("partial")
            root.resolve("hwt_chunk_short.uploading").toFile().writeText("keep")
            val unsafe = Files.createDirectory(root.resolve("hwt_chunk_${"b".repeat(32)}.uploading")).toFile()

            val failures = cleanupStaleChunkUploadFiles(root.toFile())

            assertEquals(emptyList<File>(), failures)
            assertFalse(stale.exists())
            assertFalse(Files.isRegularFile(unsafe.toPath(), LinkOption.NOFOLLOW_LINKS))
            assertTrue(unsafe.isDirectory)
            assertTrue(root.resolve("hwt_chunk_short.uploading").toFile().isFile)
        } finally {
            root.toFile().deleteRecursively()
        }
    }

    @Test
    fun staleChunkCleanupKeepsTheFileProtectedForAnActiveCommit() {
        val root = Files.createTempDirectory("hwt-chunk-commit-cleanup-test")
        try {
            val protectedFile = root.resolve("hwt_chunk_${"a".repeat(32)}.uploading").toFile()
            val stale = root.resolve("hwt_chunk_${"b".repeat(32)}.uploading").toFile()
            protectedFile.writeText("committing")
            stale.writeText("stale")

            val failures = cleanupStaleChunkUploadFiles(root.toFile(), protectedFile)

            assertEquals(emptyList<File>(), failures)
            assertTrue(protectedFile.isFile)
            assertFalse(stale.exists())
        } finally {
            root.toFile().deleteRecursively()
        }
    }

    @Test
    fun chunkRangesMergeAndReportContiguousPrefix() {
        // 乱序完成也能合并出连续前缀。
        assertEquals(12L, contiguousReceived(listOf(8L..11L, 0L..3L, 4L..7L)))
        // 有空洞时只计算从 0 开始的前缀。
        assertEquals(4L, contiguousReceived(listOf(0L..3L, 8L..11L)))
        assertEquals(0L, contiguousReceived(listOf(4L..7L)))
        assertEquals(
            listOf(0L..11L),
            mergedRanges(listOf(8L..11L, 0L..3L, 4L..7L)),
        )
        assertEquals(
            listOf(0L..3L, 8L..11L),
            mergedRanges(listOf(0L..3L, 8L..11L)),
        )
    }

    @Test
    fun directChunkWriteTargetsOffsetAndReturnsChunkDigest() {
        val root = Files.createTempDirectory("hwt-chunk-write-test")
        try {
            val target = root.resolve("session.uploading").toFile()
            val first = ByteArray(4) { it.toByte() }
            val second = ByteArray(4) { (it + 4).toByte() }

            fun digestOf(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
                .digest(bytes).joinToString("") { "%02x".format(it) }

            assertEquals(digestOf(first), writeChunkAt(ByteArrayInputStream(first), target, 0, 4))
            assertEquals(digestOf(second), writeChunkAt(ByteArrayInputStream(second), target, 4, 4))
            assertTrue(target.readBytes().contentEquals(first + second))

            // 同一偏移可以幂等重写（断线重发场景）。
            assertEquals(digestOf(second), writeChunkAt(ByteArrayInputStream(second), target, 4, 4))
            assertTrue(target.readBytes().contentEquals(first + second))
        } finally {
            root.toFile().deleteRecursively()
        }
    }

    @Test
    fun directChunkWriteRejectsTruncatedBody() {
        val root = Files.createTempDirectory("hwt-chunk-write-truncate-test")
        try {
            val target = root.resolve("session.uploading").toFile()
            val error = org.junit.Assert.assertThrows(TransferException::class.java) {
                writeChunkAt(ByteArrayInputStream(ByteArray(2)), target, 0, 4)
            }
            assertEquals("incomplete_upload", error.code)
        } finally {
            root.toFile().deleteRecursively()
        }
    }
}
