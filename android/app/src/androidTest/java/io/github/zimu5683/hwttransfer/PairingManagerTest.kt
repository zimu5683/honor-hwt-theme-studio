package io.github.zimu5683.hwttransfer

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class PairingManagerTest {
    private val context = ApplicationProvider.getApplicationContext<android.content.Context>()

    @Before
    fun clearPreferences() {
        context.getSharedPreferences("pairing", android.content.Context.MODE_PRIVATE).edit().clear().commit()
    }

    @Test
    fun pairedTokenCanBeRevoked() {
        val manager = PairingManager(context)
        val result = manager.pair(manager.code, "测试电脑")
        assertTrue(manager.isAuthorized(result.token))
        manager.revoke(result.client.tokenHash)
        assertFalse(manager.isAuthorized(result.token))
    }

    @Test
    fun wrongCodeIsRejected() {
        val manager = PairingManager(context)
        val wrongCode = if (manager.code == "000000") "000001" else "000000"
        repeat(5) {
            runCatching { manager.pair(wrongCode, "测试电脑") }
        }
        assertTrue(runCatching { manager.pair(wrongCode, "测试电脑") }.exceptionOrNull() is TransferException)
    }

    @Test
    fun pairingCodeExpiresAtExactBoundary() {
        var now = 1_000L
        val manager = PairingManager(context) { now }
        val code = manager.code
        now += Protocol.PAIR_CODE_TTL_MS

        val error = runCatching { manager.pair(code, "测试电脑") }.exceptionOrNull()
        assertEquals("pair_code_expired", (error as TransferException).code)
    }

    @Test
    fun clientNameIsNormalizedWithoutSplittingUnicodeCharacters() {
        val manager = PairingManager(context)
        val result = manager.pair(manager.code, "  电脑\n\u0000  😀😀😀  ")

        assertEquals("电脑 😀😀😀", result.client.name)
        assertTrue(manager.clients().any { it.name == result.client.name })
    }
}
