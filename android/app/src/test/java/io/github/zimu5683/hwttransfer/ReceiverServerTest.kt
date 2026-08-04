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
}
