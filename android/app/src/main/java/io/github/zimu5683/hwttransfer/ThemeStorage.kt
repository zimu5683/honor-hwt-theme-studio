package io.github.zimu5683.hwttransfer

import android.content.Context
import android.net.Uri
import android.os.Environment
import android.os.StatFs
import android.os.storage.StorageManager
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import androidx.documentfile.provider.DocumentFile
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID

private val themeInstallLock = Any()

class ThemeStorage(private val context: Context) {
    private val prefs = context.getSharedPreferences("storage", Context.MODE_PRIVATE)
    private val directDirectory = File(Environment.getExternalStorageDirectory(), "Honor/Themes")

    fun treeUri(): Uri? = prefs.getString("tree_uri", null)?.let(Uri::parse)

    fun hasSafAccess(): Boolean {
        return try {
            val uri = treeUri() ?: return false
            val persisted = context.contentResolver.persistedUriPermissions.any {
                it.uri == uri && it.isReadPermission && it.isWritePermission
            }
            persisted && DocumentFile.fromTreeUri(context, uri)?.canWrite() == true
        } catch (_: Exception) {
            false
        }
    }

    fun hasAllFilesAccess(): Boolean = Environment.isExternalStorageManager()
    fun isAvailable(): Boolean = hasSafAccess() || hasAllFilesAccess()

    fun destinationLabel(): String = when {
        hasSafAccess() -> "Honor/Themes（目录授权）"
        hasAllFilesAccess() -> directDirectory.absolutePath
        else -> "尚未授权 Honor/Themes"
    }

    fun validateAndPersistTree(uri: Uri) = synchronized(themeInstallLock) {
        val directory = DocumentFile.fromTreeUri(context, uri)
            ?: throw TransferException(503, "storage_unavailable", "无法打开所选目录")
        if (!directory.isDirectory || !directory.canWrite()) {
            throw TransferException(503, "storage_unavailable", "所选目录不可写")
        }
        if (!directory.name.equals("Themes", ignoreCase = true)) {
            throw TransferException(400, "wrong_directory", "请选择 Honor 文件夹中的 Themes 目录")
        }
        val probeName = "hwt_transfer_permission_check_${UUID.randomUUID()}.tmp"
        val probe = directory.createFile("application/octet-stream", probeName)
            ?: throw TransferException(503, "storage_unavailable", "无法在所选目录创建文件")
        var failure: Exception? = null
        try {
            context.contentResolver.openOutputStream(probe.uri, "w")?.use { it.write(byteArrayOf(0)) }
                ?: throw TransferException(503, "storage_unavailable", "无法写入所选目录")
        } catch (exc: Exception) {
            failure = exc
        }
        val deleted = try {
            probe.delete()
        } catch (_: Exception) {
            false
        }
        if (!deleted) {
            val cleanupFailure = TransferException(503, "storage_unavailable", "无法清理目录权限测试文件")
            failure?.let(cleanupFailure::addSuppressed)
            throw cleanupFailure
        }
        failure?.let { throw it }
        prefs.edit().putString("tree_uri", uri.toString()).apply()
    }

    fun clearSaf() = synchronized(themeInstallLock) {
        treeUri()?.let(::discardSafUnlocked)
    }

    fun discardSaf(uri: Uri) = synchronized(themeInstallLock) {
        discardSafUnlocked(uri)
    }

    private fun discardSafUnlocked(uri: Uri) {
        runCatching {
            context.contentResolver.releasePersistableUriPermission(
                uri,
                android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION or android.content.Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
            )
        }
        if (treeUri() == uri) {
            prefs.edit().remove("tree_uri").apply()
        }
    }

    fun importUri(uri: Uri): InstallResult {
        val resolver = context.contentResolver
        var displayName = "imported_theme.hwt"
        var declaredSize = -1L
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                displayName = cursor.getString(0) ?: displayName
                if (!cursor.isNull(1)) declaredSize = cursor.getLong(1)
            }
        }
        if (declaredSize > Protocol.MAX_FILE_SIZE) {
            throw TransferException(413, "too_large", "HWT 文件超过 1 GiB 上限")
        }
        val temp = File.createTempFile("hwt_import_", ".tmp", context.cacheDir)
        try {
            resolver.openInputStream(uri)?.use { input ->
                FileOutputStream(temp).use { output -> copyLimited(input, output) }
            } ?: throw TransferException(400, "read_failed", "无法读取所选文件")
            return install(temp, displayName, null)
        } finally {
            temp.delete()
        }
    }

    fun install(source: File, requestedName: String, expectedSha256: String?): InstallResult = synchronized(themeInstallLock) {
        try {
            val safTree = if (hasSafAccess()) treeUri() else null
            if (safTree == null && !hasAllFilesAccess()) {
                throw TransferException(503, "storage_unavailable", "Honor/Themes 授权已失效")
            }
            val name = Protocol.safeFileName(requestedName)
            Protocol.validateHwt(source)
            val digest = Protocol.sha256(source)
            if (expectedSha256 != null && !digest.equals(expectedSha256, ignoreCase = true)) {
                throw TransferException(422, "hash_mismatch", "上传文件的 SHA-256 与电脑不一致")
            }
            ensureFreeSpace(source.length(), safTree)
            if (safTree != null) installSaf(source, name, digest, safTree) else installDirect(source, name, digest)
        } catch (exc: TransferException) {
            throw exc
        } catch (_: IOException) {
            throw TransferException(503, "storage_unavailable", "无法写入 Honor/Themes 目录")
        } catch (_: SecurityException) {
            throw TransferException(503, "storage_unavailable", "Honor/Themes 目录授权已失效")
        }
    }

    private fun installDirect(source: File, name: String, digest: String): InstallResult {
        if (!directDirectory.exists() && !directDirectory.mkdirs()) {
            throw TransferException(503, "storage_unavailable", "无法创建 Honor/Themes 目录")
        }
        val target = File(directDirectory, name)
        val uploading = File.createTempFile("hwt_upload_", ".uploading", directDirectory)
        var backup: File? = null
        var backupMoved = false
        var committed = false
        var restored = false
        var published = false
        val overwritten = target.exists()
        try {
            FileInputStream(source).use { input ->
                FileOutputStream(uploading).use { output ->
                    copyLimited(input, output)
                    output.fd.sync()
                }
            }
            if (!Protocol.sha256(uploading).equals(digest, ignoreCase = true)) {
                throw TransferException(422, "hash_mismatch", "写入手机存储后的 SHA-256 不一致")
            }
            if (overwritten) {
                val candidate = File.createTempFile("hwt_backup_", ".backup", directDirectory)
                backup = candidate
                if (!candidate.delete()) {
                    throw TransferException(503, "replace_failed", "无法准备同名主题备份")
                }
                try {
                    Files.move(target.toPath(), candidate.toPath(), StandardCopyOption.ATOMIC_MOVE)
                } catch (_: Exception) {
                    Files.move(target.toPath(), candidate.toPath())
                }
                backupMoved = true
            }
            try {
                Files.move(uploading.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
            } catch (_: Exception) {
                Files.move(uploading.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
            }
            published = true
            if (!Protocol.sha256(target).equals(digest, ignoreCase = true)) {
                throw TransferException(422, "hash_mismatch", "最终主题文件 SHA-256 不一致")
            }
            committed = true
            backup?.delete()
            return InstallResult(name, "Honor/Themes/$name", source.length(), digest, overwritten)
        } catch (exc: Exception) {
            if (!committed) {
                val savedBackup = backup
                if (backupMoved && savedBackup?.exists() == true) {
                    if (published) target.delete()
                    try {
                        Files.move(savedBackup.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE)
                    } catch (_: Exception) {
                        try {
                            Files.move(savedBackup.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
                        } catch (_: Exception) {
                            throw TransferException(503, "replace_failed", "无法恢复原主题文件")
                        }
                    }
                    restored = true
                } else if (!overwritten && published) {
                    target.delete()
                } else if (!backupMoved) {
                    savedBackup?.delete()
                }
            }
            throw exc
        } finally {
            uploading.delete()
            if (committed || restored) backup?.delete()
        }
    }

    private fun installSaf(source: File, name: String, digest: String, tree: Uri): InstallResult {
        val directory = DocumentFile.fromTreeUri(context, tree)
            ?: throw TransferException(503, "storage_unavailable", "目录授权已失效")
        val uploadName = "hwt_transfer_${UUID.randomUUID()}.uploading"
        val temporary = directory.createFile("application/octet-stream", uploadName)
            ?: throw TransferException(503, "storage_unavailable", "无法在主题目录创建临时文件")
        var backup: DocumentFile? = null
        var committed = false
        var temporaryRenamed = false
        var temporaryDeleted = false
        var published: DocumentFile? = null
        try {
            context.contentResolver.openOutputStream(temporary.uri, "w")?.use { output ->
                FileInputStream(source).use { input -> copyLimited(input, output) }
            } ?: throw TransferException(503, "storage_unavailable", "无法写入主题目录")
            val writtenDigest = context.contentResolver.openInputStream(temporary.uri)?.use(::sha256Stream)
                ?: throw TransferException(503, "storage_unavailable", "无法复核写入文件")
            if (!writtenDigest.equals(digest, ignoreCase = true)) {
                throw TransferException(422, "hash_mismatch", "写入手机存储后的 SHA-256 不一致")
            }
            val existing = directory.findFile(name)
            val overwritten = existing != null
            if (existing != null) {
                val backupName = "$name.backup-${System.nanoTime()}"
                if (!existing.renameTo(backupName)) {
                    throw TransferException(503, "replace_failed", "无法安全备份同名主题文件")
                }
                backup = existing
            }
            if (temporary.renameTo(name)) {
                temporaryRenamed = true
                published = temporary
            } else {
                if (!temporary.delete()) {
                    throw TransferException(503, "rename_failed", "无法清理主题临时文件")
                }
                temporaryDeleted = true
                val finalFile = directory.createFile("application/octet-stream", name)
                    ?: throw TransferException(503, "rename_failed", "无法生成最终主题文件")
                published = finalFile
                FileInputStream(source).use { input ->
                    context.contentResolver.openOutputStream(finalFile.uri, "w")?.use { output -> copyLimited(input, output) }
                        ?: throw TransferException(503, "rename_failed", "无法写入最终主题文件")
                }
                val finalDigest = context.contentResolver.openInputStream(finalFile.uri)?.use(::sha256Stream)
                if (!finalDigest.equals(digest, ignoreCase = true)) {
                    finalFile.delete()
                    throw TransferException(422, "hash_mismatch", "最终主题文件 SHA-256 不一致")
                }
            }
            val finalFile = directory.findFile(name)
                ?: throw TransferException(503, "rename_failed", "无法确认最终主题文件")
            if (published == null) published = finalFile
            val finalDigest = context.contentResolver.openInputStream(finalFile.uri)?.use(::sha256Stream)
                ?: throw TransferException(503, "rename_failed", "无法复核最终主题文件")
            if (!finalDigest.equals(digest, ignoreCase = true)) {
                throw TransferException(422, "hash_mismatch", "最终主题文件 SHA-256 不一致")
            }
            committed = true
            backup?.delete()
            return InstallResult(name, "Honor/Themes/$name", source.length(), digest, overwritten)
        } catch (exc: Exception) {
            if (!committed) {
                published?.delete()
                val restored = backup?.renameTo(name) ?: true
                if (!restored) {
                    val restoreFailure = TransferException(503, "replace_failed", "无法恢复原主题文件")
                    restoreFailure.addSuppressed(exc)
                    throw restoreFailure
                }
            }
            throw exc
        } finally {
            if (!temporaryRenamed && !temporaryDeleted) temporary.delete()
            if (committed) backup?.delete()
        }
    }

    private fun ensureFreeSpace(size: Long, safTree: Uri?) {
        val root = safTree?.let(::safStorageRoot) ?: Environment.getExternalStorageDirectory()
        val available = root?.let {
            runCatching { StatFs(it.path).availableBytes }.getOrDefault(0L)
        } ?: 0L
        if (!Protocol.hasSufficientSpace(available, size)) {
            throw TransferException(507, "no_space", "手机存储空间不足")
        }
    }

    private fun safStorageRoot(uri: Uri): File? {
        val documentId = runCatching { DocumentsContract.getTreeDocumentId(uri) }.getOrNull() ?: return null
        val volumeId = documentId.substringBefore(':', missingDelimiterValue = "")
        if (volumeId.isBlank()) return null
        if (volumeId.equals("primary", ignoreCase = true)) {
            return Environment.getExternalStorageDirectory()
        }
        return runCatching {
            context.getSystemService(StorageManager::class.java)
                ?.storageVolumes
                ?.firstOrNull { it.uuid?.equals(volumeId, ignoreCase = true) == true }
                ?.directory
        }.getOrNull()
    }

    private fun copyLimited(input: java.io.InputStream, output: java.io.OutputStream) {
        val buffer = ByteArray(1024 * 1024)
        var total = 0L
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            total += count
            if (total > Protocol.MAX_FILE_SIZE) throw TransferException(413, "too_large", "HWT 文件超过 1 GiB 上限")
            output.write(buffer, 0, count)
        }
    }

    private fun sha256Stream(input: java.io.InputStream): String {
        val digest = java.security.MessageDigest.getInstance("SHA-256")
        val buffer = ByteArray(1024 * 1024)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            digest.update(buffer, 0, count)
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
