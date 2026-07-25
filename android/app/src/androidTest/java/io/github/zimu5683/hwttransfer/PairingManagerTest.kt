package io.github.zimu5683.hwttransfer

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
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
}
