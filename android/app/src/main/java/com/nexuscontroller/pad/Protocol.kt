package com.nexuscontroller.pad

/**
 * Nexus Controller wire protocol, version 2.
 *
 * Pure Kotlin: no Android and no socket dependencies, so every byte layout here is
 * covered by plain JUnit tests.
 *
 * Transport is TCP on port 6000 with TCP_NODELAY. All multi-byte integers are
 * big-endian, signed 8-bit values are two's complement.
 */

/** Device type announced in HELLO and used to shape the INPUT payload. */
enum class ControllerType(val wire: Int, val label: String) {
    XBOX360(0x00, "Xbox 360"),
    DUALSHOCK4(0x01, "DualShock 4"),
    BUZZ(0x02, "Buzz");

    /** Xbox and DualShock share the on-screen gamepad; Buzz has its own layout family. */
    val isGamepad: Boolean get() = this != BUZZ

    companion object {
        fun fromWire(v: Int): ControllerType? = entries.firstOrNull { it.wire == v }

        /** Tolerant lookup used for the `controller_type` preference. */
        fun fromStorage(v: String?): ControllerType =
            entries.firstOrNull { it.name.equals(v, ignoreCase = true) } ?: XBOX360
    }
}

/** Why the server refused the handshake. */
enum class RejectReason(val code: Int, val message: String) {
    UNSUPPORTED_VERSION(0x01, "Server speaks a different protocol version. Update the PC app."),
    INVALID_TOKEN(0x02, "Invalid pairing code — scan the QR code again."),
    SERVER_FULL(0x03, "Server full (4/4). Disconnect another pad first."),
    MALFORMED_HANDSHAKE(0x04, "Server rejected the handshake (malformed request)."),
    UNAUTHENTICATED(0x05, "Server rejected the handshake (not authenticated)."),
    RATE_LIMITED(0x06, "Too many failed attempts. Wait a minute and try again.");

    companion object {
        fun fromCode(code: Int): RejectReason? = entries.firstOrNull { it.code == code }

        fun messageFor(code: Int): String =
            fromCode(code)?.message ?: "Connection refused by the server (code 0x%02X).".format(code)
    }
}

/** Payload of `0x11 WELCOME`. */
data class Welcome(val slot: Int, val features: Int) {
    val rumbleAvailable: Boolean get() = features and 0x01 != 0
    val ledAvailable: Boolean get() = features and 0x02 != 0
}

/** A server seen on the LAN via UDP discovery. */
data class DiscoveredServer(
    val name: String,
    val port: Int,
    val tokenRequired: Boolean
)

/** Where and how to connect: parsed from a QR payload, discovery or manual entry. */
data class ConnectionTarget(
    val ip: String,
    val port: Int = Protocol.DEFAULT_PORT,
    val token: String = ""
)

object Protocol {

    const val VERSION = 0x02
    const val DEFAULT_PORT = 6000
    const val DISCOVERY_PORT = 6001

    // client -> server
    const val OP_INPUT = 0x01
    const val OP_TEXT = 0x02
    const val OP_MOUSE = 0x04
    const val OP_SCROLL = 0x05
    const val OP_HELLO = 0x10
    const val OP_PING = 0xF0

    // server -> client
    const val OP_RUMBLE = 0x03
    const val OP_WELCOME = 0x11
    const val OP_LED = 0x12
    const val OP_REJECT = 0x1F
    const val OP_PONG = 0xF1

    /** INPUT flags byte (offset 12 of the payload). */
    const val FLAG_MOUSE_MODE = 0x01
    const val FLAG_GYRO_VALID = 0x02

    // buttons_low, gamepad device types
    const val BTN_A = 0x01
    const val BTN_B = 0x02
    const val BTN_X = 0x04
    const val BTN_Y = 0x08
    const val BTN_LB = 0x10
    const val BTN_RB = 0x20
    const val BTN_BACK = 0x40
    const val BTN_START = 0x80

    // buttons_low, Buzz device type (semantic; the server maps them to XInput)
    const val BUZZ_RED = 0x01
    const val BUZZ_YELLOW = 0x02
    const val BUZZ_GREEN = 0x04
    const val BUZZ_ORANGE = 0x08
    const val BUZZ_BLUE = 0x10
    const val BUZZ_MASK = 0x1F

    // buttons_high, identical for every device type
    const val BTN_L3 = 0x01
    const val BTN_R3 = 0x02
    const val DPAD_UP = 0x04
    const val DPAD_DOWN = 0x08
    const val DPAD_LEFT = 0x10
    const val DPAD_RIGHT = 0x20
    const val BTN_GUIDE = 0x40

    const val MAX_TOKEN_LEN = 64
    const val MAX_NAME_LEN = 32

    const val DISCOVERY_REQUEST = "NEXUSPAD_DISCOVER_V2"
    const val DISCOVERY_RESPONSE_PREFIX = "NEXUSPAD_SERVER_V2"
    const val QR_PREFIX = "NEXUSPAD2"

    /**
     * `0x10 HELLO`: version, device type, length-prefixed token, length-prefixed name.
     * Token is truncated to 64 ASCII bytes, name to 32 UTF-8 bytes.
     */
    fun hello(type: ControllerType, token: String, deviceName: String): ByteArray {
        val tokenBytes = token.toByteArray(Charsets.US_ASCII).let {
            if (it.size > MAX_TOKEN_LEN) it.copyOf(MAX_TOKEN_LEN) else it
        }
        val nameBytes = truncateUtf8(deviceName, MAX_NAME_LEN)
        val out = ByteArray(5 + tokenBytes.size + nameBytes.size)
        out[0] = OP_HELLO.toByte()
        out[1] = VERSION.toByte()
        out[2] = type.wire.toByte()
        out[3] = tokenBytes.size.toByte()
        tokenBytes.copyInto(out, 4)
        out[4 + tokenBytes.size] = nameBytes.size.toByte()
        nameBytes.copyInto(out, 5 + tokenBytes.size)
        return out
    }

    /**
     * `0x01 INPUT`, 1 opcode byte + 16 payload bytes.
     *
     * Sticks are given in the UI's `0..255` space (127 = centre, Y growing downwards)
     * and converted here to the wire's int8 space (0 = centre, + = right / + = up).
     * In [ControllerType.BUZZ] mode only the five semantic buzz bits survive; sticks,
     * triggers, gyro and `buttons_high` are forced to zero — the server does the
     * XInput translation.
     */
    fun input(
        type: ControllerType,
        leftXUi: Int, leftYUi: Int,
        rightXUi: Int, rightYUi: Int,
        buttonsLow: Int, buttonsHigh: Int,
        leftTrigger: Int, rightTrigger: Int,
        gyroRoll: Int = 0, gyroPitch: Int = 0,
        flags: Int = 0
    ): ByteArray {
        val buzz = type == ControllerType.BUZZ
        val lx = if (buzz) 0 else axisFromUi(leftXUi)
        val ly = if (buzz) 0 else -axisFromUi(leftYUi)   // UI Y grows down, wire Y grows up
        val rx = if (buzz) 0 else axisFromUi(rightXUi)
        val ry = if (buzz) 0 else -axisFromUi(rightYUi)
        val low = if (buzz) buttonsLow and BUZZ_MASK else buttonsLow and 0xFF
        val high = if (buzz) 0 else buttonsHigh and 0xFF
        val lt = if (buzz) 0 else leftTrigger.coerceIn(0, 255)
        val rt = if (buzz) 0 else rightTrigger.coerceIn(0, 255)
        val roll = if (buzz) 0 else clampInt16(gyroRoll)
        val pitch = if (buzz) 0 else clampInt16(gyroPitch)
        val f = if (buzz) 0 else flags and 0xFF

        val p = ByteArray(17)
        p[0] = OP_INPUT.toByte()
        p[1] = clampAxis(lx).toByte()
        p[2] = clampAxis(ly).toByte()
        p[3] = clampAxis(rx).toByte()
        p[4] = clampAxis(ry).toByte()
        p[5] = low.toByte()
        p[6] = high.toByte()
        p[7] = lt.toByte()
        p[8] = rt.toByte()
        p[9] = (roll shr 8).toByte()
        p[10] = roll.toByte()
        p[11] = (pitch shr 8).toByte()
        p[12] = pitch.toByte()
        p[13] = f.toByte()
        // p[14..16] reserved, already zero
        return p
    }

    /** `0x02 TEXT`: one length byte then UTF-8 bytes (truncated to 255). */
    fun text(value: String): ByteArray {
        val body = truncateUtf8(value, 255)
        val p = ByteArray(2 + body.size)
        p[0] = OP_TEXT.toByte()
        p[1] = body.size.toByte()
        body.copyInto(p, 2)
        return p
    }

    /** `0x04 MOUSE`: dx, dy as int8 plus a button bitmask. */
    fun mouse(dx: Int, dy: Int, buttons: Int): ByteArray =
        byteArrayOf(OP_MOUSE.toByte(), clampAxis(dx).toByte(), clampAxis(dy).toByte(), (buttons and 0xFF).toByte())

    /** `0x05 SCROLL`: dx, dy as int8. */
    fun scroll(dx: Int, dy: Int): ByteArray =
        byteArrayOf(OP_SCROLL.toByte(), clampAxis(dx).toByte(), clampAxis(dy).toByte())

    /** `0xF0 PING`: uint32 sequence number. */
    fun ping(seq: Long): ByteArray {
        val p = ByteArray(5)
        p[0] = OP_PING.toByte()
        writeUInt32(p, 1, seq)
        return p
    }

    fun writeUInt32(target: ByteArray, offset: Int, value: Long) {
        val v = value and 0xFFFFFFFFL
        target[offset] = (v ushr 24).toByte()
        target[offset + 1] = (v ushr 16).toByte()
        target[offset + 2] = (v ushr 8).toByte()
        target[offset + 3] = v.toByte()
    }

    fun readUInt32(source: ByteArray, offset: Int): Long =
        ((source[offset].toLong() and 0xFF) shl 24) or
            ((source[offset + 1].toLong() and 0xFF) shl 16) or
            ((source[offset + 2].toLong() and 0xFF) shl 8) or
            (source[offset + 3].toLong() and 0xFF)

    /** Parses the two payload bytes of `0x11 WELCOME`. */
    fun parseWelcome(slot: Int, features: Int): Welcome = Welcome(slot and 0xFF, features and 0xFF)

    /**
     * Parses `NEXUSPAD_SERVER_V2|<name>|<port>|<0|1>`.
     * Returns null for anything that is not a well-formed v2 response.
     */
    fun parseDiscoveryResponse(raw: String?): DiscoveredServer? {
        val line = raw?.trim()?.trimEnd(' ') ?: return null
        val parts = line.split('|')
        if (parts.size != 4) return null
        if (parts[0] != DISCOVERY_RESPONSE_PREFIX) return null
        val name = parts[1].trim()
        if (name.isEmpty()) return null
        val port = parts[2].trim().toIntOrNull() ?: return null
        if (port !in 1..65535) return null
        val flag = parts[3].trim()
        if (flag != "0" && flag != "1") return null
        return DiscoveredServer(name, port, flag == "1")
    }

    /** `-128` is not a legal axis value on the wire. */
    fun clampAxis(v: Int): Int = v.coerceIn(-127, 127)

    fun clampInt16(v: Int): Int = v.coerceIn(-32768, 32767)

    /** Maps the app's `0..255` axis (127 = centre) onto the wire's `-127..127`. */
    fun axisFromUi(v: Int): Int {
        val c = v.coerceIn(0, 255)
        return clampAxis(Math.round((c - 127.5f) / 127.5f * 127f))
    }

    private fun truncateUtf8(value: String, maxBytes: Int): ByteArray {
        val bytes = value.toByteArray(Charsets.UTF_8)
        if (bytes.size <= maxBytes) return bytes
        // Never cut in the middle of a multi-byte sequence.
        var end = maxBytes
        while (end > 0 && (bytes[end].toInt() and 0xC0) == 0x80) end--
        return bytes.copyOf(end)
    }
}

/**
 * Parsing of everything the user can hand us as a connection target: the QR payload
 * from the PC dashboard, or a bare IPv4 typed by hand.
 */
object QrPayload {

    private val IPV4 = Regex("""^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$""")
    private val TOKEN = Regex("""^[0-9a-fA-F]{1,64}$""")

    /**
     * Accepts `NEXUSPAD2:<ip>:<port>:<token>` or a bare IPv4 address.
     * Returns null for anything else — callers must surface an error rather than dial.
     */
    fun parse(raw: String?): ConnectionTarget? {
        val value = raw?.trim() ?: return null
        if (value.isEmpty()) return null

        if (value.startsWith("${Protocol.QR_PREFIX}:")) {
            val parts = value.split(':')
            if (parts.size != 4) return null
            val ip = parts[1].trim()
            if (!isIpv4(ip)) return null
            val port = parts[2].trim().toIntOrNull() ?: return null
            if (port !in 1..65535) return null
            val token = parts[3].trim()
            if (!TOKEN.matches(token)) return null
            return ConnectionTarget(ip, port, token)
        }

        if (isIpv4(value)) return ConnectionTarget(value, Protocol.DEFAULT_PORT, "")
        return null
    }

    fun isIpv4(value: String): Boolean {
        val m = IPV4.matchEntire(value) ?: return false
        return (1..4).all { i ->
            val part = m.groupValues[i]
            // reject "01" style octets and out-of-range values
            (part.length == 1 || part[0] != '0') && part.toInt() in 0..255
        }
    }
}
