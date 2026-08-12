package com.nexuscontroller.pad

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {

    private fun ByteArray.u(i: Int) = this[i].toInt() and 0xFF

    // ---------------------------------------------------------------- HELLO

    @Test
    fun `hello encodes xbox360 byte for byte`() {
        val packet = Protocol.hello(ControllerType.XBOX360, "abc", "Pad")
        assertArrayEquals(
            byteArrayOf(
                0x10, 0x02, 0x00,
                3, 'a'.code.toByte(), 'b'.code.toByte(), 'c'.code.toByte(),
                3, 'P'.code.toByte(), 'a'.code.toByte(), 'd'.code.toByte()
            ),
            packet
        )
    }

    @Test
    fun `hello encodes every device type`() {
        assertEquals(0x00, Protocol.hello(ControllerType.XBOX360, "", "").u(2))
        assertEquals(0x01, Protocol.hello(ControllerType.DUALSHOCK4, "", "").u(2))
        assertEquals(0x02, Protocol.hello(ControllerType.BUZZ, "", "").u(2))
    }

    @Test
    fun `hello with empty token and name is five bytes`() {
        val packet = Protocol.hello(ControllerType.BUZZ, "", "")
        assertArrayEquals(byteArrayOf(0x10, 0x02, 0x02, 0x00, 0x00), packet)
    }

    @Test
    fun `hello truncates oversized token and name`() {
        val token = "f".repeat(200)
        val name = "n".repeat(100)
        val packet = Protocol.hello(ControllerType.XBOX360, token, name)
        assertEquals(Protocol.MAX_TOKEN_LEN, packet.u(3))
        assertEquals(Protocol.MAX_NAME_LEN, packet.u(4 + Protocol.MAX_TOKEN_LEN))
        assertEquals(5 + Protocol.MAX_TOKEN_LEN + Protocol.MAX_NAME_LEN, packet.size)
    }

    @Test
    fun `hello never splits a multi byte character`() {
        // 32 two-byte characters = 64 bytes, must be cut to 32 bytes = 16 characters.
        val packet = Protocol.hello(ControllerType.XBOX360, "", "ą".repeat(32))
        val nameLen = packet.u(4)
        assertEquals(32, nameLen)
        val name = String(packet, 5, nameLen, Charsets.UTF_8)
        assertEquals("ą".repeat(16), name)
    }

    // ---------------------------------------------------------------- INPUT

    @Test
    fun `input is seventeen bytes with zero reserved tail`() {
        val p = Protocol.input(ControllerType.XBOX360, 127, 127, 127, 127, 0, 0, 0, 0)
        assertEquals(17, p.size)
        assertEquals(Protocol.OP_INPUT, p.u(0))
        assertEquals(0, p.u(14))
        assertEquals(0, p.u(15))
        assertEquals(0, p.u(16))
    }

    @Test
    fun `neutral sticks are zero not minus one`() {
        val p = Protocol.input(ControllerType.XBOX360, 127, 127, 127, 127, 0, 0, 0, 0)
        assertEquals(0, p[1].toInt())
        assertEquals(0, p[2].toInt())
        assertEquals(0, p[3].toInt())
        assertEquals(0, p[4].toInt())
    }

    @Test
    fun `stick extremes map to plus and minus 127`() {
        val p = Protocol.input(ControllerType.XBOX360, 255, 255, 0, 0, 0, 0, 0, 0)
        assertEquals(127, p[1].toInt())      // right
        assertEquals(-127, p[2].toInt())     // UI y=255 is down -> wire -127
        assertEquals(-127, p[3].toInt())
        assertEquals(127, p[4].toInt())      // UI y=0 is up -> wire +127
    }

    @Test
    fun `no axis ever encodes minus 128`() {
        for (v in 0..255) {
            val p = Protocol.input(ControllerType.XBOX360, v, v, v, v, 0, 0, 0, 0)
            for (i in 1..4) {
                assertTrue("axis $i for ui=$v was ${p[i]}", p[i].toInt() != -128)
                assertTrue(p[i].toInt() in -127..127)
            }
        }
    }

    @Test
    fun `clampAxis rejects minus 128`() {
        assertEquals(-127, Protocol.clampAxis(-128))
        assertEquals(-127, Protocol.clampAxis(-9999))
        assertEquals(127, Protocol.clampAxis(9999))
        assertEquals(5, Protocol.clampAxis(5))
    }

    @Test
    fun `buttons triggers and gyro land at the documented offsets`() {
        val p = Protocol.input(
            ControllerType.XBOX360,
            127, 127, 127, 127,
            buttonsLow = 0xA5, buttonsHigh = 0x5A,
            leftTrigger = 200, rightTrigger = 255,
            gyroRoll = -300, gyroPitch = 1000,
            flags = Protocol.FLAG_GYRO_VALID
        )
        assertEquals(0xA5, p.u(5))
        assertEquals(0x5A, p.u(6))
        assertEquals(200, p.u(7))
        assertEquals(255, p.u(8))
        assertEquals(-300, ((p.u(9) shl 8) or p.u(10)).toShort().toInt())
        assertEquals(1000, ((p.u(11) shl 8) or p.u(12)).toShort().toInt())
        assertEquals(Protocol.FLAG_GYRO_VALID, p.u(13))
    }

    @Test
    fun `flags byte carries mouse mode and gyro valid`() {
        val both = Protocol.input(
            ControllerType.XBOX360, 127, 127, 127, 127, 0, 0, 0, 0,
            flags = Protocol.FLAG_MOUSE_MODE or Protocol.FLAG_GYRO_VALID
        )
        assertEquals(0x03, both.u(13))
        val none = Protocol.input(ControllerType.XBOX360, 127, 127, 127, 127, 0, 0, 0, 0)
        assertEquals(0x00, none.u(13))
    }

    @Test
    fun `triggers are clamped to the byte range`() {
        val p = Protocol.input(ControllerType.XBOX360, 127, 127, 127, 127, 0, 0, -5, 900)
        assertEquals(0, p.u(7))
        assertEquals(255, p.u(8))
    }

    // ---------------------------------------------------------------- PING / PONG

    @Test
    fun `ping encodes a big endian uint32 sequence`() {
        assertArrayEquals(byteArrayOf(0xF0.toByte(), 0, 0, 0, 1), Protocol.ping(1))
        assertArrayEquals(
            byteArrayOf(0xF0.toByte(), 0xDE.toByte(), 0xAD.toByte(), 0xBE.toByte(), 0xEF.toByte()),
            Protocol.ping(0xDEADBEEFL)
        )
    }

    @Test
    fun `ping and pong sequence numbers round trip`() {
        listOf(0L, 1L, 255L, 65_536L, 0xFFFFFFFFL).forEach { seq ->
            val packet = Protocol.ping(seq)
            assertEquals(seq, Protocol.readUInt32(packet, 1))
        }
    }

    @Test
    fun `readUInt32 keeps values unsigned`() {
        val buf = byteArrayOf(0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte(), 0xFF.toByte())
        assertEquals(4_294_967_295L, Protocol.readUInt32(buf, 0))
    }

    // ---------------------------------------------------------------- TEXT / MOUSE / SCROLL

    @Test
    fun `text is length prefixed utf8`() {
        val p = Protocol.text("hi")
        assertArrayEquals(byteArrayOf(0x02, 2, 'h'.code.toByte(), 'i'.code.toByte()), p)
    }

    @Test
    fun `mouse and scroll clamp their deltas`() {
        val m = Protocol.mouse(-500, 500, 0x03)
        assertArrayEquals(byteArrayOf(0x04, -127, 127, 0x03), m)
        val s = Protocol.scroll(-128, 3)
        assertArrayEquals(byteArrayOf(0x05, -127, 3), s)
    }

    // ---------------------------------------------------------------- CONFIG / SET_CONFIG

    @Test
    fun `config opcodes match the spec`() {
        assertEquals(0x06, Protocol.OP_CONFIG)
        assertEquals(0x13, Protocol.OP_SET_CONFIG)
        assertEquals(16384, Protocol.MAX_CONFIG_BYTES)
    }

    @Test
    fun `config frames a small document byte for byte`() {
        val p = Protocol.configJson("""{"v":1}""")
        assertArrayEquals(
            byteArrayOf(
                0x06, 0x00, 0x07,
                '{'.code.toByte(), '"'.code.toByte(), 'v'.code.toByte(), '"'.code.toByte(),
                ':'.code.toByte(), '1'.code.toByte(), '}'.code.toByte()
            ),
            p
        )
    }

    @Test
    fun `config length is a big endian uint16`() {
        // 300 bytes cannot be expressed in the single length byte TEXT uses.
        val body = "x".repeat(300)
        val p = Protocol.configJson(body)
        assertEquals(Protocol.OP_CONFIG, p.u(0))
        assertEquals(0x01, p.u(1))
        assertEquals(0x2C, p.u(2))
        assertEquals(300, Protocol.readUInt16(p, 1))
        assertEquals(303, p.size)
        assertEquals(body, String(p, 3, 300, Charsets.UTF_8))
    }

    @Test
    fun `config length counts utf8 bytes and not characters`() {
        val p = Protocol.configJson("ą".repeat(10))     // 2 bytes each
        assertEquals(20, Protocol.readUInt16(p, 1))
        assertEquals(23, p.size)
    }

    @Test
    fun `config length reaches the whole uint16 range`() {
        listOf(0, 1, 255, 256, 4096, Protocol.MAX_CONFIG_BYTES).forEach { n ->
            val p = Protocol.configJson("x".repeat(n))
            assertEquals("length $n", n, Protocol.readUInt16(p, 1))
            assertEquals(n + 3, p.size)
        }
    }

    @Test
    fun `an empty config body is legal`() {
        assertArrayEquals(byteArrayOf(0x06, 0x00, 0x00), Protocol.configJson(""))
    }

    @Test(expected = IllegalArgumentException::class)
    fun `an over long config body is rejected rather than truncated`() {
        Protocol.configJson("x".repeat(Protocol.MAX_CONFIG_BYTES + 1))
    }

    @Test
    fun `configJsonOrNull drops an over long body instead of throwing`() {
        assertNull(Protocol.configJsonOrNull("x".repeat(Protocol.MAX_CONFIG_BYTES + 1)))
        // Multi-byte characters count for what they weigh on the wire.
        assertNull(Protocol.configJsonOrNull("ą".repeat(Protocol.MAX_CONFIG_BYTES / 2 + 1)))
        assertNotNull(Protocol.configJsonOrNull("x".repeat(Protocol.MAX_CONFIG_BYTES)))
    }

    @Test
    fun `uint16 helpers round trip the full range`() {
        listOf(0, 1, 255, 256, 4096, 16384, 65535).forEach { v ->
            val buf = ByteArray(2)
            Protocol.writeUInt16(buf, 0, v)
            assertEquals(v, Protocol.readUInt16(buf, 0))
        }
    }

    @Test
    fun `a set_config body decodes as utf8`() {
        // What the read loop does with the bytes that follow the uint16 length.
        val json = """{"v":1,"name":"Ania ą"}"""
        val framed = Protocol.configJson(json)
        val length = Protocol.readUInt16(framed, 1)
        val body = framed.copyOfRange(3, 3 + length)
        assertEquals(json, Protocol.decodeConfigBody(body))
    }

    @Test
    fun `a real configuration document fits the frame`() {
        val doc = ConfigDocument(
            type = ControllerType.XBOX360,
            name = "Player 1",
            screen = ScreenSize(2400, 1080),
            layout = LayoutStore.defaults(ControllerType.XBOX360),
            settings = ConfigSettings(true, 0.85f, false, 0.4f, true, "Dark")
        )
        val packet = Protocol.configJsonOrNull(ConfigCodec.encode(doc))
        assertNotNull(packet)
        assertEquals(Protocol.OP_CONFIG, packet!!.u(0))
        assertEquals(packet.size - 3, Protocol.readUInt16(packet, 1))
    }

    // ---------------------------------------------------------------- WELCOME / REJECT

    @Test
    fun `welcome exposes slot and feature bits`() {
        val w = Protocol.parseWelcome(2, 0x03)
        assertEquals(2, w.slot)
        assertTrue(w.rumbleAvailable)
        assertTrue(w.ledAvailable)

        val none = Protocol.parseWelcome(0, 0)
        assertEquals(0, none.slot)
        assertFalse(none.rumbleAvailable)
        assertFalse(none.ledAvailable)

        val ledOnly = Protocol.parseWelcome(3, 0x02)
        assertFalse(ledOnly.rumbleAvailable)
        assertTrue(ledOnly.ledAvailable)
    }

    @Test
    fun `every documented reject reason parses`() {
        assertEquals(RejectReason.UNSUPPORTED_VERSION, RejectReason.fromCode(0x01))
        assertEquals(RejectReason.INVALID_TOKEN, RejectReason.fromCode(0x02))
        assertEquals(RejectReason.SERVER_FULL, RejectReason.fromCode(0x03))
        assertEquals(RejectReason.MALFORMED_HANDSHAKE, RejectReason.fromCode(0x04))
        assertEquals(RejectReason.UNAUTHENTICATED, RejectReason.fromCode(0x05))
        assertEquals(RejectReason.RATE_LIMITED, RejectReason.fromCode(0x06))
        assertNull(RejectReason.fromCode(0x00))
        assertNull(RejectReason.fromCode(0x99))
    }

    @Test
    fun `every reject reason points at a string resource`() {
        // The text itself lives in strings.xml, translated; what this file can
        // still guarantee is that each code has a message to resolve at all.
        // (It also used to promise "4/4" for a server that now holds eight.)
        RejectReason.entries.forEach { assertTrue(it.messageRes != 0) }
        assertEquals(
            RejectReason.entries.size,
            RejectReason.entries.map { it.messageRes }.toSet().size
        )
    }

    // ---------------------------------------------------------------- discovery

    @Test
    fun `discovery request string matches the spec`() {
        assertEquals("NEXUSPAD_DISCOVER_V2", Protocol.DISCOVERY_REQUEST)
    }

    @Test
    fun `discovery response parses name port and token flag`() {
        val s = Protocol.parseDiscoveryResponse("NEXUSPAD_SERVER_V2|Gaming Rig|6000|1")!!
        assertEquals("Gaming Rig", s.name)
        assertEquals(6000, s.port)
        assertTrue(s.tokenRequired)

        val open = Protocol.parseDiscoveryResponse("NEXUSPAD_SERVER_V2|PC|7001|0")!!
        assertEquals(7001, open.port)
        assertFalse(open.tokenRequired)
    }

    @Test
    fun `malformed discovery responses are rejected`() {
        listOf(
            null,
            "",
            "   ",
            "NEXUSPAD_SERVER|PC|6000|1",           // v1 prefix
            "NEXUSPAD_SERVER_V2|PC|6000",          // missing field
            "NEXUSPAD_SERVER_V2|PC|6000|1|extra",  // extra field
            "NEXUSPAD_SERVER_V2||6000|1",          // empty name
            "NEXUSPAD_SERVER_V2|PC|abc|1",         // non-numeric port
            "NEXUSPAD_SERVER_V2|PC|0|1",           // port out of range
            "NEXUSPAD_SERVER_V2|PC|70000|1",
            "NEXUSPAD_SERVER_V2|PC|6000|2",        // bad flag
            "garbage",
            // NUL padding. Nothing produces it — the server sends exactly the
            // bytes it built and the phone decodes exactly the bytes it received
            // (`String(data, 0, length)`) — and a reply that carries it is not a
            // reply we should be believing. There used to be a trimEnd here for
            // it, guarding against a sender that does not exist.
            "NEXUSPAD_SERVER_V2|PC|6000|1\u0000\u0000"
        ).forEach { assertNull("should reject: $it", Protocol.parseDiscoveryResponse(it)) }
    }
}
