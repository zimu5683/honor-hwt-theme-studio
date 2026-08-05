package io.github.zimu5683.hwttransfer

import java.io.File
import java.nio.file.Files
import java.nio.file.LinkOption
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
}
