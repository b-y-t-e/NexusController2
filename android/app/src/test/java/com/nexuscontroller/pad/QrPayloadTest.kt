package com.nexuscontroller.pad

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class QrPayloadTest {

    private val token = "0123456789abcdef0123456789abcdef"

    @Test
    fun `parses a full pairing payload`() {
        val t = QrPayload.parse("NEXUSPAD2:192.168.1.20:6000:$token")!!
        assertEquals("192.168.1.20", t.ip)
        assertEquals(6000, t.port)
        assertEquals(token, t.token)
    }

    @Test
    fun `parses a non default port`() {
        val t = QrPayload.parse("NEXUSPAD2:10.0.0.5:7010:$token")!!
        assertEquals(7010, t.port)
    }

    @Test
    fun `surrounding whitespace is tolerated`() {
        assertEquals("192.168.0.1", QrPayload.parse("  192.168.0.1  ")!!.ip)
        assertEquals(6000, QrPayload.parse(" NEXUSPAD2:192.168.0.1:6000:$token ")!!.port)
    }

    @Test
    fun `bare ipv4 gets the default port and no token`() {
        val t = QrPayload.parse("127.0.0.1")!!
        assertEquals("127.0.0.1", t.ip)
        assertEquals(Protocol.DEFAULT_PORT, t.port)
        assertEquals("", t.token)
    }

    @Test
    fun `garbage is rejected`() {
        listOf(
            null, "", "   ",
            "hello world",
            "http://192.168.1.20:6000",
            "192.168.1",                                   // too few octets
            "192.168.1.1.1",                               // too many octets
            "192.168.1.256",                               // octet out of range
            "192.168.01.1",                                // leading zero
            "NEXUSPAD:192.168.1.20:6000:$token",           // v1 prefix
            "NEXUSPAD2:192.168.1.20:6000",                 // missing token
            "NEXUSPAD2:192.168.1.20:6000:$token:extra",    // extra field
            "NEXUSPAD2:not-an-ip:6000:$token",
            "NEXUSPAD2:192.168.1.20:0:$token",             // port out of range
            "NEXUSPAD2:192.168.1.20:99999:$token",
            "NEXUSPAD2:192.168.1.20:abc:$token",
            "NEXUSPAD2:192.168.1.20:6000:not-hex!",
            "NEXUSPAD2:192.168.1.20:6000:",                // empty token
            "NEXUSPAD2:192.168.1.20:6000:${"a".repeat(65)}" // token too long
        ).forEach { assertNull("should reject: $it", QrPayload.parse(it)) }
    }

    @Test
    fun `ipv4 validation`() {
        assertTrue(QrPayload.isIpv4("0.0.0.0"))
        assertTrue(QrPayload.isIpv4("255.255.255.255"))
        assertFalse(QrPayload.isIpv4("::1"))
        assertFalse(QrPayload.isIpv4("1.2.3.4 "))
        assertFalse(QrPayload.isIpv4(""))
    }
}
