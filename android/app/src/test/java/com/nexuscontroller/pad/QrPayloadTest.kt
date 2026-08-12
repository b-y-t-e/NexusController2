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

    /**
     * The PC really does emit this when "Require pairing token" is off (§8), so
     * refusing it made the QR code unusable in that mode.
     */
    @Test
    fun `an empty token means the server does not require pairing`() {
        val t = QrPayload.parse("NEXUSPAD2:192.168.1.20:6000:")!!
        assertEquals("192.168.1.20", t.ip)
        assertEquals(6000, t.port)
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
            "NEXUSPAD2:192.168.1.20:6000:${"a".repeat(65)}" // token too long
        ).forEach { assertNull("should reject: $it", QrPayload.parse(it)) }
    }

    // ------------------------------------------------- connecting after a scan

    /** Stands in for the phone's per-host token store. */
    private fun stored(vararg pairs: Pair<String, String>): (String) -> String {
        val map = pairs.toMap()
        return { ip -> map[ip] ?: "" }
    }

    @Test
    fun `a server found by scanning is dialled with the token kept for it`() {
        """A scan yields an address and nothing else - the discovery reply says
        whether a token is needed, never which one - so the credentials can only
        come from an earlier pairing with that same host."""
        val target = QrPayload.targetFor(
            "192.168.1.20",
            stored("192.168.1.20" to token, "192.168.1.99" to "deadbeef")
        )!!
        assertEquals("192.168.1.20", target.ip)
        assertEquals(Protocol.DEFAULT_PORT, target.port)
        assertEquals(token, target.token)
    }

    @Test
    fun `a token kept for one server is never sent to another`() {
        val target = QrPayload.targetFor("192.168.1.77", stored("192.168.1.20" to token))!!
        assertEquals("", target.token)
    }

    @Test
    fun `scanning a server nobody paired with carries no token at all`() {
        """And is refused by the server, which is the point: the QR code is the
        only place a token comes from, so an unpaired scan has to fail rather
        than quietly connect."""
        val target = QrPayload.targetFor("192.168.1.20", stored())!!
        assertEquals("", target.token)
    }

    @Test
    fun `a token in the QR code beats the one on file`() {
        """Rescanning is how a phone is re-paired after the PC issues a new
        token. Preferring the stored copy would make the new code unusable."""
        val fresh = "b".repeat(32)
        val target = QrPayload.targetFor(
            "NEXUSPAD2:192.168.1.20:6000:$fresh",
            stored("192.168.1.20" to token)
        )!!
        assertEquals(fresh, target.token)
    }

    @Test
    fun `a QR code from a server with pairing off falls back to what we have`() {
        """The payload really does end in a bare colon then, and the server
        ignores the token either way - so the stored one is harmless, and it is
        still there if pairing is turned back on with the same token."""
        val target = QrPayload.targetFor(
            "NEXUSPAD2:192.168.1.20:6000:",
            stored("192.168.1.20" to token)
        )!!
        assertEquals(token, target.token)
    }

    @Test
    fun `the port a scan reports is kept, not the default`() {
        val target = QrPayload.targetFor("NEXUSPAD2:192.168.1.20:7100:", stored())!!
        assertEquals(7100, target.port)
    }

    @Test
    fun `nonsense is still nonsense, and the store is never consulted`() {
        var asked = false
        val target = QrPayload.targetFor("not an address") { asked = true; token }
        assertNull(target)
        assertFalse("no lookup should happen for input that names nothing", asked)
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
