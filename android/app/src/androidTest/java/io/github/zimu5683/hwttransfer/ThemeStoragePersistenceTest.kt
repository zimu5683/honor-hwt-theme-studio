package io.github.zimu5683.hwttransfer

import android.content.Context
import android.net.Uri
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ThemeStoragePersistenceTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Before
    fun clearPreferences() {
        context.getSharedPreferences("storage", Context.MODE_PRIVATE).edit().clear().commit()
    }

    @Test
    fun safTreeUriIsPersistedBeforeCommitReturns() {
        val uri = Uri.parse("content://com.android.externalstorage.documents/tree/primary%3AHonor%2FThemes")
        val prefs = context.getSharedPreferences("storage", Context.MODE_PRIVATE)

        assertTrue(persistSafTreeUri(prefs, uri))
        assertEquals(uri, ThemeStorage(context).treeUri())
    }

    @Test
    fun replacingSafSelectionReleasesOnlyThePreviousDifferentUri() {
        val previous = Uri.parse("content://provider/tree/old")
        val selected = Uri.parse("content://provider/tree/new")

        assertEquals(previous, safUriToRelease(previous, selected))
        assertNull(safUriToRelease(previous, previous))
        assertNull(safUriToRelease(null, selected))
    }
}
