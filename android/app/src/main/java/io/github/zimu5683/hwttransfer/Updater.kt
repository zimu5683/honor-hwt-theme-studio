package io.github.zimu5683.hwttransfer

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** 一条可安装的更新。 */
data class AndroidUpdate(
    val version: String,
    val versionCode: Int,
    val apkUrl: String,
    val apkSha256: String,
    val body: String,
)

class UpdateException(message: String) : Exception(message)

/**
 * GitHub Releases 自更新。清单读取自 latest-android.json（发布时由 CI 生成），
 * 版本比较基于 versionCode，下载后先做 SHA-256 校验再交给系统安装器。
 */
object Updater {

    private const val MANIFEST_URL =
        "https://github.com/zimu5683/honor-hwt-theme-studio/releases/latest/download/latest-android.json"

    // GitHub 直连在国内经常不可达，依次尝试：直连 → 国内加速镜像。
    // 前缀只到镜像域名，候选 = 前缀 + 完整 GitHub URL（ghproxy 类服务要求保持原路径）。
    private val MIRROR_PREFIXES = listOf(
        "https://ghproxy.net/",
        "https://gh-proxy.com/",
        "https://ghfast.top/",
    )

    /** 返回 [直连, 镜像1, 镜像2...]，镜像只对 github.com 的 URL 生效。 */
    private fun urlCandidates(url: String): List<String> {
        if (!url.startsWith("https://github.com/")) return listOf(url)
        return listOf(url) + MIRROR_PREFIXES.map { prefix -> "$prefix$url" }
    }

    /** 拉取更新清单；无更新返回 null，网络/清单异常抛 [UpdateException]。 */
    suspend fun checkForUpdate(): AndroidUpdate? = withContext(Dispatchers.IO) {
        val payload = fetchManifest()
        val version = payload.optString("version").trim()
        val versionCode = payload.optInt("versionCode", 0)
        val apkUrl = payload.optString("apk_url").trim()
        val apkSha256 = payload.optString("apk_sha256").trim().lowercase()
        val body = payload.optString("body").trim()
        if (version.isEmpty() || versionCode <= 0 || !apkUrl.startsWith("https://") ||
            !apkSha256.matches(Regex("[0-9a-f]{64}"))
        ) {
            throw UpdateException("更新清单无效")
        }
        if (versionCode <= BuildConfig.VERSION_CODE) null
        else AndroidUpdate(version, versionCode, apkUrl, apkSha256, body)
    }

    /** 下载并校验 APK，返回本地文件。 */
    suspend fun downloadApk(
        context: Context,
        update: AndroidUpdate,
        onProgress: (Long, Long) -> Unit,
    ): File = withContext(Dispatchers.IO) {
        val dir = File(context.cacheDir, "updates").apply { mkdirs() }
        val target = File(dir, "HwtThemeReceiver-${update.version}.apk")
        val part = File(dir, "${target.name}.part")
        var lastError: Exception? = null
        for (candidate in urlCandidates(update.apkUrl)) {
            part.delete()
            try {
                downloadFrom(candidate, part, update.apkSha256, onProgress)
                if (target.exists()) target.delete()
                if (!part.renameTo(target)) throw UpdateException("无法保存更新包")
                return@withContext target
            } catch (exc: Exception) {
                lastError = exc
            }
        }
        throw lastError ?: UpdateException("下载失败")
    }

    private fun downloadFrom(
        url: String,
        part: File,
        expectedSha256: String,
        onProgress: (Long, Long) -> Unit,
    ) {
        var connection: HttpURLConnection? = null
        try {
            connection = (URL(url).openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 15_000
                readTimeout = 60_000
                setRequestProperty("Accept", "application/octet-stream")
                setRequestProperty("User-Agent", "HwtThemeReceiver/${BuildConfig.VERSION_NAME}")
            }
            val code = connection.responseCode
            if (code !in 200..299) throw UpdateException("下载失败：HTTP $code")
            val total = connection.contentLengthLong.coerceAtLeast(0L)
            connection.inputStream.use { input ->
                FileOutputStream(part).use { output ->
                    val buffer = ByteArray(1024 * 1024)
                    var received = 0L
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        received += read
                        onProgress(received, total)
                    }
                }
            }
            if (sha256(part) != expectedSha256) {
                throw UpdateException("下载校验失败：SHA-256 不一致")
            }
        } finally {
            connection?.disconnect()
        }
    }

    /** 通过系统安装器安装 APK；返回 false 表示需先授予“允许安装未知来源”。 */
    fun installApk(context: Context, apk: File): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !context.packageManager.canRequestPackageInstalls()
        ) {
            openInstallPermissionSettings(context)
            return false
        }
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", apk)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        context.startActivity(intent)
        return true
    }

    private fun openInstallPermissionSettings(context: Context) {
        runCatching {
            context.startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))
            )
        }
    }

    private fun fetchManifest(): JSONObject {
        var lastError: Exception? = null
        for (candidate in urlCandidates(MANIFEST_URL)) {
            for (attempt in 0 until 2) {
                try {
                    var connection: HttpURLConnection? = null
                    try {
                        connection = (URL(candidate).openConnection() as HttpURLConnection).apply {
                            requestMethod = "GET"
                            connectTimeout = 10_000
                            readTimeout = 15_000
                            setRequestProperty("Accept", "application/json")
                            setRequestProperty("User-Agent", "HwtThemeReceiver/${BuildConfig.VERSION_NAME}")
                        }
                        val code = connection.responseCode
                        if (code !in 200..299) throw UpdateException("检查更新失败：HTTP $code")
                        val text = connection.inputStream.bufferedReader().use { it.readText() }
                        return JSONObject(text)
                    } finally {
                        connection?.disconnect()
                    }
                } catch (exc: Exception) {
                    lastError = exc
                    if (attempt == 0) Thread.sleep(400)
                }
            }
        }
        throw lastError ?: UpdateException("检查更新失败")
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
    }
}
