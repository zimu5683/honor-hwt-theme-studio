package io.github.zimu5683.hwttransfer

import java.io.File
import java.io.FileInputStream
import java.security.MessageDigest
import java.util.zip.ZipFile

object Protocol {
    const val VERSION = 1
    const val DISCOVERY_PORT = 48620
    const val HTTP_PORT = 48621
    const val DISCOVERY_REQUEST = "HWTSTUDIO_DISCOVER_V1"
    const val MAX_FILE_SIZE = 1024L * 1024L * 1024L
    const val IDLE_TIMEOUT_MS = 30L * 60L * 1000L
    const val PAIR_CODE_TTL_MS = 5L * 60L * 1000L

    fun safeFileName(input: String): String {
        var name = input.substringAfterLast('/').substringAfterLast('\\')
            .replace(Regex("[^\\p{L}\\p{N}_.-]+"), "_")
            .trim('.', '_')
        if (name.isBlank()) name = "theme"
        if (!name.endsWith(".hwt", ignoreCase = true)) name += ".hwt"
        return name
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
                if (archive.getEntry("description.xml") == null) {
                    throw TransferException(422, "invalid_hwt", "HWT 中缺少 description.xml")
                }
            }
        } catch (exc: TransferException) {
            throw exc
        } catch (exc: Exception) {
            throw TransferException(422, "invalid_hwt", "HWT ZIP 结构损坏：${exc.message}")
        }
    }
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
