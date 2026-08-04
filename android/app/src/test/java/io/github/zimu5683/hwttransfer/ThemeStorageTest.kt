package io.github.zimu5683.hwttransfer

import java.io.IOException
import java.nio.file.Files
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ThemeStorageTest {
    @Test
    fun directTargetOnlyAllowsMissingAndRegularFiles() {
        val root = Files.createTempDirectory("hwt-storage-test")
        try {
            val missing = root.resolve("missing.hwt").toFile()
            val regular = Files.createFile(root.resolve("theme.hwt")).toFile()
            val directory = Files.createDirectory(root.resolve("theme-dir.hwt")).toFile()

            assertTrue(isReplaceableDirectThemeTarget(missing))
            assertTrue(isReplaceableDirectThemeTarget(regular))
            assertFalse(isReplaceableDirectThemeTarget(directory))
        } finally {
            root.toFile().deleteRecursively()
        }
    }

    @Test
    fun directTargetRejectsSymbolicLinksWhenSupported() {
        val root = Files.createTempDirectory("hwt-storage-link-test")
        try {
            val regular = Files.createFile(root.resolve("theme.hwt"))
            val link = root.resolve("link.hwt")
            try {
                Files.createSymbolicLink(link, regular.fileName)
            } catch (_: UnsupportedOperationException) {
                return
            } catch (_: IOException) {
                return
            }
            assertFalse(isReplaceableDirectThemeTarget(link.toFile()))
        } finally {
            root.toFile().deleteRecursively()
        }
    }
}
