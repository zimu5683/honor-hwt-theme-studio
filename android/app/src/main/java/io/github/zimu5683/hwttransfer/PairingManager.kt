package io.github.zimu5683.hwttransfer

import android.content.Context
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.UUID

data class PairedClient(val name: String, val tokenHash: String, val pairedAt: Long)
data class PairResult(val token: String, val client: PairedClient)

private fun normalizeClientName(value: String): String {
    val normalized = StringBuilder()
    var pendingSpace = false
    var index = 0
    while (index < value.length) {
        val codePoint = value.codePointAt(index)
        index += Character.charCount(codePoint)
        if (Character.isWhitespace(codePoint) || Character.isSpaceChar(codePoint) || Character.isISOControl(codePoint)) {
            pendingSpace = normalized.isNotEmpty()
            continue
        }
        if (pendingSpace) normalized.append(' ')
        normalized.appendCodePoint(codePoint)
        pendingSpace = false
    }
    val valueWithoutTrailingSpace = normalized.toString().trim()
    var end = 0
    var count = 0
    while (end < valueWithoutTrailingSpace.length && count < Protocol.MAX_CLIENT_NAME_CODE_POINTS) {
        end += Character.charCount(valueWithoutTrailingSpace.codePointAt(end))
        count += 1
    }
    return valueWithoutTrailingSpace.substring(0, end)
}

class PairingManager(context: Context, private val clock: () -> Long = System::currentTimeMillis) {
    private val prefs = context.getSharedPreferences("pairing", Context.MODE_PRIVATE)
    private val random = SecureRandom()
    private val failedAttempts = ArrayDeque<Long>()

    val deviceId: String = synchronized(storageLock) {
        prefs.getString("device_id", null) ?: UUID.randomUUID().toString().also {
            prefs.edit().putString("device_id", it).apply()
        }
    }

    @Volatile var code: String = ""
        private set
    @Volatile var codeExpiresAt: Long = 0L
        private set

    init {
        regenerateCode()
    }

    @Synchronized
    fun regenerateCode(): String {
        code = random.nextInt(1_000_000).toString().padStart(6, '0')
        codeExpiresAt = clock() + Protocol.PAIR_CODE_TTL_MS
        failedAttempts.clear()
        return code
    }

    @Synchronized
    fun pair(inputCode: String, clientName: String): PairResult {
        val now = clock()
        while (failedAttempts.isNotEmpty() && now - failedAttempts.first() >= 60_000L) {
            failedAttempts.removeFirst()
        }
        if (failedAttempts.size >= 5) {
            throw TransferException(429, "pair_rate_limited", "配对失败次数过多，请一分钟后重试")
        }
        if (now >= codeExpiresAt) {
            throw TransferException(401, "pair_code_expired", "配对码已过期，请在手机上刷新")
        }
        if (inputCode != code) {
            failedAttempts.addLast(now)
            throw TransferException(401, "invalid_pair_code", "配对码错误")
        }
        val tokenBytes = ByteArray(32).also(random::nextBytes)
        val token = Base64.encodeToString(tokenBytes, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        val safeName = normalizeClientName(clientName).ifBlank { "大雪主题编辑器" }
        val client = PairedClient(safeName, hashToken(token), now)
        synchronized(storageLock) {
            val clients = readClients().filterNot { it.name == safeName }.toMutableList().apply { add(client) }
            saveClients(clients)
        }
        regenerateCode()
        return PairResult(token, client)
    }

    fun isAuthorized(token: String?): Boolean {
        if (token.isNullOrBlank()) return false
        val candidate = hashToken(token)
        return clients().any { MessageDigest.isEqual(it.tokenHash.toByteArray(), candidate.toByteArray()) }
    }

    fun clients(): List<PairedClient> {
        return synchronized(storageLock) { readClients() }
    }

    companion object {
        private val storageLock = Any()

        internal fun parseClients(raw: String): List<PairedClient> {
            val array = runCatching { JSONArray(raw) }.getOrNull() ?: return emptyList()
            return buildList {
                for (index in 0 until array.length()) {
                    val item = array.optJSONObject(index) ?: continue
                    val rawName = item.opt("name")
                    val rawTokenHash = item.opt("token_hash")
                    val rawPairedAt = item.opt("paired_at")
                    if (rawName !is String || rawTokenHash !is String) continue
                    val name = rawName.trim()
                    val tokenHash = rawTokenHash.lowercase()
                    val pairedAt = when (rawPairedAt) {
                        is Int -> rawPairedAt.toLong()
                        is Long -> rawPairedAt
                        else -> continue
                    }
                    if (
                        name.isBlank() ||
                        name != normalizeClientName(name) ||
                        !TOKEN_HASH_PATTERN.matches(tokenHash) ||
                        pairedAt < 0L
                    ) continue
                    add(PairedClient(name, tokenHash, pairedAt))
                }
            }
        }

        private val TOKEN_HASH_PATTERN = Regex("[0-9a-f]{64}")
    }

    fun revoke(tokenHash: String) {
        synchronized(storageLock) {
            saveClients(readClients().filterNot { it.tokenHash == tokenHash })
        }
    }

    fun revokeAll() {
        synchronized(storageLock) {
            saveClients(emptyList())
        }
    }

    private fun readClients(): List<PairedClient> {
        val raw = prefs.getString("clients", "[]") ?: "[]"
        return parseClients(raw)
    }

    private fun saveClients(clients: List<PairedClient>) {
        val array = JSONArray()
        clients.forEach {
            array.put(JSONObject().put("name", it.name).put("token_hash", it.tokenHash).put("paired_at", it.pairedAt))
        }
        prefs.edit().putString("clients", array.toString()).apply()
    }

    private fun hashToken(token: String): String = MessageDigest.getInstance("SHA-256")
        .digest(token.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }

}
