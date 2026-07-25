package io.github.zimu5683.hwttransfer

import android.content.Context
import android.net.Uri
import android.os.Environment
import android.os.StatFs
import android.provider.OpenableColumns
import androidx.documentfile.provider.DocumentFile
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption

class ThemeStorage(private val context: Context) {
    private val prefs = context.getSharedPreferences("storage", Context.MODE_PRIVATE)
    private val directDirectory = File(Environment.getExternalStorageDirectory(), "Honor/Themes")

    fun treeUri(): Uri? = prefs.getString("tree_uri", null)?.let(Uri::parse)

    fun hasSafAccess(): Boolean {
        val uri = treeUri() ?: return false
        val persisted = context.contentResolver.persistedUriPermissions.any {
            it.uri == uri && it.isReadPermission && it.isWritePermission
        }
        return persisted && DocumentFile.fromTreeUri(context, uri)?.canWrite() == true
    }

    fun hasAllFilesAccess(): Boolean = Environment.isExternalStorageManager()
    fun isAvailable(): Boolean = hasSafAccess() || hasAllFilesAccess()

    fun destinationLabel(): String = when {
        hasSafAccess() -> "Honor/Themes（目录授权）"
        hasAllFilesAccess() -> directDirectory.absolutePath
        else -> "尚未授权 Honor/Themes"
    }

    fun validateAndPersistTree(uri: Uri) {
        val directory = DocumentFile.fromTreeUri(context, uri)
            ?: throw TransferException(503, "storage_unavailable", "无法打开所选目录")
        if (!directory.isDirectory || !directory.canWrite()) {
            throw TransferException(503, "storage_unavailable", "所选目录不可写")
        }
        if (!directory.name.equals("Themes", ignoreCase = true)) {
            throw TransferException(400, "wrong_directory", "请选择 Honor 文件夹中的 Themes 目录")
        }
        val probeName = "hwt_transfer_permission_check.tmp"
        directory.findFile(probeName)?.delete()
        val probe = directory.createFile("application/octet-stream", probeName)
            ?: throw TransferException(503, "storage_unavailable", "无法在所选目录创建文件")
        context.contentResolver.openOutputStream(probe.uri, "w")?.use { it.write(byteArrayOf(0)) }
            ?: throw TransferException(503, "storage_unavailable", "无法写入所选目录")
        if (!probe.delete()) {
            throw TransferException(503, "storage_unavailable", "目录可写，但无法删除测试文件")
        }
        prefs.edit().putString("tree_uri", uri.toString()).apply()
    }

    fun clearSaf() {
        treeUri()?.let { uri ->
            runCatching {
                context.contentResolver.releasePersistableUriPermission(
                    uri,
                    android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION or android.content.Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
                )
            }
        }
        prefs.edit().remove("tree_uri").apply()
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

    fun install(source: File, requestedName: String, expectedSha256: String?): InstallResult {
        if (!isAvailable()) throw TransferException(503, "storage_unavailable", "Honor/Themes 授权已失效")
        val name = Protocol.safeFileName(requestedName)
        Protocol.validateHwt(source)
        val digest = Protocol.sha256(source)
        if (expectedSha256 != null && !digest.equals(expectedSha256, ignoreCase = true)) {
            throw TransferException(422, "hash_mismatch", "上传文件的 SHA-256 与电脑不一致")
        }
        ensureFreeSpace(source.length())
        return if (hasSafAccess()) installSaf(source, name, digest) else installDirect(source, name, digest)
    }

    private fun installDirect(source: File, name: String, digest: String): InstallResult {
        if (!directDirectory.exists() && !directDirectory.mkdirs()) {
            throw TransferException(503, "storage_unavailable", "无法创建 ${directDirectory.absolutePath}")
        }
        val target = File(directDirectory, name)
        val uploading = File(directDirectory, "$name.uploading")
        uploading.delete()
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
            val overwritten = target.exists()
            try {
                Files.move(uploading.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
            } catch (_: Exception) {
                Files.move(uploading.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
            }
            return InstallResult(name, "Honor/Themes/$name", source.length(), digest, overwritten)
        } finally {
            uploading.delete()
        }
    }

    private fun installSaf(source: File, name: String, digest: String): InstallResult {
        val directory = DocumentFile.fromTreeUri(context, treeUri()!!)
            ?: throw TransferException(503, "storage_unavailable", "目录授权已失效")
        val uploadName = "$name.uploading"
        directory.findFile(uploadName)?.delete()
        val temporary = directory.createFile("application/octet-stream", uploadName)
            ?: throw TransferException(503, "storage_unavailable", "无法在主题目录创建临时文件")
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
            if (existing != null && !existing.delete()) {
                throw TransferException(503, "replace_failed", "无法替换同名主题文件")
            }
            if (!temporary.renameTo(name)) {
                val finalFile = directory.createFile("application/octet-stream", name)
                    ?: throw TransferException(503, "rename_failed", "无法生成最终主题文件")
                context.contentResolver.openInputStream(temporary.uri)?.use { input ->
                    context.contentResolver.openOutputStream(finalFile.uri, "w")?.use { output -> copyLimited(input, output) }
                        ?: throw TransferException(503, "rename_failed", "无法写入最终主题文件")
                } ?: throw TransferException(503, "rename_failed", "无法读取临时主题文件")
                val finalDigest = context.contentResolver.openInputStream(finalFile.uri)?.use(::sha256Stream)
                if (!finalDigest.equals(digest, ignoreCase = true)) {
                    finalFile.delete()
                    throw TransferException(422, "hash_mismatch", "最终主题文件 SHA-256 不一致")
                }
                temporary.delete()
            }
            return InstallResult(name, "Honor/Themes/$name", source.length(), digest, overwritten)
        } catch (exc: Exception) {
            temporary.delete()
            throw exc
        }
    }

    private fun ensureFreeSpace(size: Long) {
        val available = runCatching { StatFs(Environment.getExternalStorageDirectory().path).availableBytes }.getOrDefault(0L)
        if (available in 1 until (size + 16L * 1024L * 1024L)) {
            throw TransferException(507, "no_space", "手机存储空间不足")
        }
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
