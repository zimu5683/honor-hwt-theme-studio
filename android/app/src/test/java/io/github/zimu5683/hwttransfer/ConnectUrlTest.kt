package io.github.zimu5683.hwttransfer

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ConnectUrlTest {
    @Test
    fun parsesStandardIpv4WithPort() {
        val result = parseConnectUrl("hwtstudio://192.168.0.77:48624?s=abc123")
        assertEquals(Triple("192.168.0.77", 48624, "abc123"), result)
    }

    @Test
    fun parsesDefaultPortWhenOmitted() {
        val result = parseConnectUrl("hwtstudio://10.1.1.2?s=xyz")
        assertEquals(Triple("10.1.1.2", 48621, "xyz"), result)
    }

    @Test
    fun parsesIpv6Bracketed() {
        val result = parseConnectUrl("hwtstudio://[fe80::1]:48624?s=tok")
        assertEquals(Triple("fe80::1", 48624, "tok"), result)
    }

    @Test
    fun rejectsWrongScheme() {
        assertNull(parseConnectUrl("http://192.168.0.1:48621?s=abc"))
        assertNull(parseConnectUrl("weixin://x"))
    }

    @Test
    fun rejectsMissingSession() {
        assertNull(parseConnectUrl("hwtstudio://192.168.0.77:48624"))
        assertNull(parseConnectUrl("hwtstudio://192.168.0.77:48624?s="))
    }

    @Test
    fun rejectsInvalidPort() {
        assertNull(parseConnectUrl("hwtstudio://192.168.0.77:99999?s=a"))
        assertNull(parseConnectUrl("hwtstudio://192.168.0.77:0?s=a"))
        assertNull(parseConnectUrl("hwtstudio://192.168.0.77:port?s=a"))
    }

    @Test
    fun rejectsBlankHost() {
        assertNull(parseConnectUrl("hwtstudio://:48624?s=a"))
        assertNull(parseConnectUrl("hwtstudio://"))
    }
}
