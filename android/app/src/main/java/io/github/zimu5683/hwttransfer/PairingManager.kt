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

class PairingManager(context: Context, private val clock: () -> Long = System::currentTimeMillis) {
    private val prefs = context.getSharedPreferences("pairing", Context.MODE_PRIVATE)
    private val random = SecureRandom()
    private val failedAttempts = ArrayDeque<Long>()

    val deviceId: String = prefs.getString("device_id", null) ?: UUID.randomUUID().toString().also {
        prefs.edit().putString("device_id", it).apply()
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
        code = "%06d".format(random.nextInt(1_000_000))
        codeExpiresAt = clock() + Protocol.PAIR_CODE_TTL_MS
        failedAttempts.clear()
        return code
    }

    @Synchronized
    fun pair(inputCode: String, clientName: String): PairResult {
        val now = clock()
        while (failedAttempts.isNotEmpty() && now - failedAttempts.first() > 60_000L) {
            failedAttempts.removeFirst()
        }
        if (failedAttempts.size >= 5) {
            throw TransferException(429, "pair_rate_limited", "配对失败次数过多，请一分钟后重试")
        }
        if (now > codeExpiresAt) {
            throw TransferException(401, "pair_code_expired", "配对码已过期，请在手机上刷新")
        }
        if (inputCode != code) {
            failedAttempts.addLast(now)
            throw TransferException(401, "invalid_pair_code", "配对码错误")
        }
        val tokenBytes = ByteArray(32).also(random::nextBytes)
        val token = Base64.encodeToString(tokenBytes, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        val safeName = clientName.trim().take(60).ifBlank { "大雪主题编辑器" }
        val client = PairedClient(safeName, hashToken(token), now)
        val clients = clients().filterNot { it.name == safeName }.toMutableList().apply { add(client) }
        saveClients(clients)
        regenerateCode()
        return PairResult(token, client)
    }

    fun isAuthorized(token: String?): Boolean {
        if (token.isNullOrBlank()) return false
        val candidate = hashToken(token)
        return clients().any { MessageDigest.isEqual(it.tokenHash.toByteArray(), candidate.toByteArray()) }
    }

    fun clients(): List<PairedClient> {
        val raw = prefs.getString("clients", "[]") ?: "[]"
        return try {
            val array = JSONArray(raw)
            buildList {
                for (index in 0 until array.length()) {
                    val item = array.getJSONObject(index)
                    add(PairedClient(item.getString("name"), item.getString("token_hash"), item.optLong("paired_at")))
                }
            }
        } catch (_: Exception) {
            emptyList()
        }
    }

    fun revoke(tokenHash: String) {
        saveClients(clients().filterNot { it.tokenHash == tokenHash })
    }

    fun revokeAll() {
        saveClients(emptyList())
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
