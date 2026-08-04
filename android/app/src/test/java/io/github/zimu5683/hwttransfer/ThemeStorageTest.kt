package io.github.zimu5683.hwttransfer

import java.io.IOException
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.assertThrows
import org.junit.Test

class ThemeStorageTest {
    @Test
    fun safRenameStateFailsClosedForAmbiguousProviderResults() {
        assertEquals(SafRenameState.MOVED, classifySafRenameState(sourceExists = false, targetExists = true))
        assertEquals(SafRenameState.NOT_MOVED, classifySafRenameState(sourceExists = true, targetExists = false))
        assertEquals(SafRenameState.AMBIGUOUS, classifySafRenameState(sourceExists = true, targetExists = true))
        assertEquals(SafRenameState.AMBIGUOUS, classifySafRenameState(sourceExists = false, targetExists = false))
    }

    @Test
    fun directBackupPrefixIsStableAndThemeSpecific() {
        val first = directThemeBackupPrefix("主题一.hwt")
        val same = directThemeBackupPrefix("主题一.hwt")
        val other = directThemeBackupPrefix("主题二.hwt")

        assertTrue(first.startsWith("hwt_backup_"))
        assertTrue(first.length > "hwt_backup_".length)
        assertTrue(first == same)
        assertFalse(first == other)
    }

    @Test
    fun directRecoveryRestoresMatchingBackupAndKeepsUnknownLegacyBackup() {
        val root = Files.createTempDirectory("hwt-recovery-test")
        try {
            val name = "theme.hwt"
            val target = root.resolve(name).toFile()
            val backup = root.resolve(
                "${directThemeBackupPrefix(name)}old.backup",
            ).toFile()
            val legacy = root.resolve("hwt_backup_legacy.backup").toFile()
            backup.writeText("old-theme", StandardCharsets.UTF_8)
            legacy.writeText("legacy-theme", StandardCharsets.UTF_8)

            recoverDirectThemeArtifacts(root.toFile(), target, name)

            assertTrue(target.isFile)
            assertTrue(target.readText(StandardCharsets.UTF_8) == "old-theme")
            assertFalse(backup.exists())
            assertTrue(legacy.isFile)
        } finally {
            root.toFile().deleteRecursively()
        }
    }

    @Test
    fun staleDirectUploadsAreRemovedButUnsafeObjectsAreRejected() {
        val root = Files.createTempDirectory("hwt-upload-cleanup-test")
        try {
            val stale = root.resolve("hwt_upload_old.uploading").toFile()
            stale.writeText("partial", StandardCharsets.UTF_8)

            cleanupStaleDirectThemeUploads(root.toFile())

            assertFalse(stale.exists())
        } finally {
            root.toFile().deleteRecursively()
        }

        val unsafeRoot = Files.createTempDirectory("hwt-upload-unsafe-test")
        try {
            val unsafe = Files.createDirectory(unsafeRoot.resolve("hwt_upload_dir.uploading")).toFile()

            val error = assertThrows(TransferException::class.java) {
                cleanupStaleDirectThemeUploads(unsafeRoot.toFile())
            }

            assertTrue(error.code == "replace_failed")
            assertTrue(unsafe.isDirectory)
        } finally {
            unsafeRoot.toFile().deleteRecursively()
        }
    }

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
