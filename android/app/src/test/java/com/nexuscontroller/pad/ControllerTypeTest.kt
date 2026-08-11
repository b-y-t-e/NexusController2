package com.nexuscontroller.pad

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ControllerTypeTest {

    private fun ByteArray.u(i: Int) = this[i].toInt() and 0xFF

    @Test
    fun `wire values match the protocol table`() {
        assertEquals(0, ControllerType.XBOX360.wire)
        assertEquals(1, ControllerType.DUALSHOCK4.wire)
        assertEquals(2, ControllerType.BUZZ.wire)
    }

    @Test
    fun `wire values round trip`() {
        ControllerType.entries.forEach {
            assertEquals(it, ControllerType.fromWire(it.wire))
        }
        assertNull(ControllerType.fromWire(3))
        assertNull(ControllerType.fromWire(-1))
    }

    @Test
    fun `storage lookup falls back to xbox`() {
        assertEquals(ControllerType.BUZZ, ControllerType.fromStorage("BUZZ"))
        assertEquals(ControllerType.DUALSHOCK4, ControllerType.fromStorage("dualshock4"))
        assertEquals(ControllerType.XBOX360, ControllerType.fromStorage(null))
        assertEquals(ControllerType.XBOX360, ControllerType.fromStorage("nonsense"))
    }

    @Test
    fun `only buzz is outside the gamepad family`() {
        assertTrue(ControllerType.XBOX360.isGamepad)
        assertTrue(ControllerType.DUALSHOCK4.isGamepad)
        assertFalse(ControllerType.BUZZ.isGamepad)
    }

    @Test
    fun `buzz semantic bits match the protocol table`() {
        assertEquals(0x01, Protocol.BUZZ_RED)
        assertEquals(0x02, Protocol.BUZZ_YELLOW)
        assertEquals(0x04, Protocol.BUZZ_GREEN)
        assertEquals(0x08, Protocol.BUZZ_ORANGE)
        assertEquals(0x10, Protocol.BUZZ_BLUE)
    }

    @Test
    fun `buzz input zeroes sticks triggers gyro and buttons high`() {
        val p = Protocol.input(
            ControllerType.BUZZ,
            leftXUi = 0, leftYUi = 255, rightXUi = 255, rightYUi = 0,
            buttonsLow = Protocol.BUZZ_BLUE,
            buttonsHigh = 0xFF,
            leftTrigger = 255, rightTrigger = 255,
            gyroRoll = 1234, gyroPitch = -1234,
            flags = Protocol.FLAG_MOUSE_MODE or Protocol.FLAG_GYRO_VALID
        )
        assertEquals(Protocol.OP_INPUT, p.u(0))
        for (i in 1..4) assertEquals("axis $i", 0, p[i].toInt())
        assertEquals(Protocol.BUZZ_BLUE, p.u(4 + 1))
        assertEquals(0, p.u(6))    // buttons_high
        assertEquals(0, p.u(7))    // left trigger
        assertEquals(0, p.u(8))    // right trigger
        for (i in 9..16) assertEquals("byte $i", 0, p.u(i))
    }

    @Test
    fun `buzz input keeps only the five semantic bits`() {
        val all = Protocol.input(
            ControllerType.BUZZ, 127, 127, 127, 127,
            buttonsLow = 0xFF, buttonsHigh = 0, leftTrigger = 0, rightTrigger = 0
        )
        assertEquals(Protocol.BUZZ_MASK, all.u(5))
        assertEquals(0x1F, all.u(5))
    }

    @Test
    fun `buzz does not pre map to xinput`() {
        // Red must stay bit0 on the wire; translating it to RIGHT_SHOULDER is the PC's job.
        val red = Protocol.input(
            ControllerType.BUZZ, 127, 127, 127, 127,
            buttonsLow = Protocol.BUZZ_RED, buttonsHigh = 0, leftTrigger = 0, rightTrigger = 0
        )
        assertEquals(0x01, red.u(5))
    }

    @Test
    fun `gamepad types share the identical wire layout`() {
        val xbox = Protocol.input(
            ControllerType.XBOX360, 200, 30, 60, 220,
            buttonsLow = 0x0F, buttonsHigh = Protocol.BTN_GUIDE, leftTrigger = 10, rightTrigger = 20
        )
        val ds4 = Protocol.input(
            ControllerType.DUALSHOCK4, 200, 30, 60, 220,
            buttonsLow = 0x0F, buttonsHigh = Protocol.BTN_GUIDE, leftTrigger = 10, rightTrigger = 20
        )
        assertTrue(xbox.contentEquals(ds4))
    }

    @Test
    fun `face button positions keep their bits across types`() {
        assertEquals(0x01, FacePosition.BOTTOM.mask)
        assertEquals(0x02, FacePosition.RIGHT.mask)
        assertEquals(0x04, FacePosition.LEFT.mask)
        assertEquals(0x08, FacePosition.TOP.mask)
    }

    @Test
    fun `glyph labels differ per type but not the bits`() {
        assertEquals("A", Glyphs.faceLetter(ControllerType.XBOX360, FacePosition.BOTTOM))
        assertEquals("Y", Glyphs.faceLetter(ControllerType.XBOX360, FacePosition.TOP))
        assertEquals("CROSS", Glyphs.faceLetter(ControllerType.DUALSHOCK4, FacePosition.BOTTOM))
        assertEquals("TRIANGLE", Glyphs.faceLetter(ControllerType.DUALSHOCK4, FacePosition.TOP))

        assertEquals("LB", Glyphs.bumper(ControllerType.XBOX360, true))
        assertEquals("R1", Glyphs.bumper(ControllerType.DUALSHOCK4, false))
        assertEquals("LT", Glyphs.trigger(ControllerType.XBOX360, true))
        assertEquals("R2", Glyphs.trigger(ControllerType.DUALSHOCK4, false))
        assertEquals("BACK", Glyphs.center(ControllerType.XBOX360, "SHARE"))
        assertEquals("START", Glyphs.center(ControllerType.XBOX360, "OPTIONS"))
        assertEquals("SHARE", Glyphs.center(ControllerType.DUALSHOCK4, "SHARE"))
        assertEquals("OPTIONS", Glyphs.center(ControllerType.DUALSHOCK4, "OPTIONS"))
    }
}
