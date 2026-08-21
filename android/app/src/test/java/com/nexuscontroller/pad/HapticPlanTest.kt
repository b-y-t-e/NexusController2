package com.nexuscontroller.pad

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Three phones, three ways of feeling nothing, none of them an exception.
 *
 * A tablet with no motor, a Huawei that cannot be asked for a strength, and an
 * old phone that predates the whole amplitude API. The rule for each is here,
 * where it can be checked without holding the device.
 */
class HapticPlanTest {

    private fun plan(
        sdk: Int = 33,
        vibrator: Boolean = true,
        amplitude: Boolean = true,
        predefined: Boolean = true,
        duration: Long = 40,
        strength: Int = 216,
    ) = HapticPlan.of(sdk, vibrator, amplitude, predefined, duration, strength)

    @Test
    fun `a device with no motor is asked for nothing`() {
        // Most tablets. The app used to keep a switch that said "on" and did
        // nothing at all, for ever, without a word anywhere.
        assertEquals(HapticPlan.None, plan(vibrator = false))
    }

    @Test
    fun `a phone with strength control gets the strength it was asked for`() {
        assertEquals(HapticPlan.OneShot(40, 216), plan())
    }

    @Test
    fun `strength is never allowed out of range`() {
        assertEquals(HapticPlan.OneShot(40, 255), plan(strength = 9000))
        assertEquals(HapticPlan.OneShot(40, 1), plan(strength = 0))
    }

    @Test
    fun `a tap on hardware without strength control uses the OEM click`() {
        // The effect a vendor's own haptics engine implements, and on several
        // EMUI builds the one thing that reliably fires.
        assertEquals(HapticPlan.Predefined(HapticPlan.EFFECT_CLICK), plan(amplitude = false))
    }

    @Test
    fun `a rumble on the same hardware asks for its default strength`() {
        // Too long to be a click, and a number it would ignore is worse than
        // saying "however hard you can".
        assertEquals(
            HapticPlan.OneShot(200, HapticPlan.DEFAULT_AMPLITUDE),
            plan(amplitude = false, duration = 200)
        )
    }

    @Test
    fun `without createPredefined even a tap falls back to the default strength`() {
        assertEquals(
            HapticPlan.OneShot(40, HapticPlan.DEFAULT_AMPLITUDE),
            plan(sdk = 26, amplitude = false, predefined = false)
        )
    }

    @Test
    fun `before Oreo length is the only dial there is`() {
        assertEquals(HapticPlan.Legacy(40), plan(sdk = 21, amplitude = false, predefined = false))
    }

    @Test
    fun `no motor beats every other rule`() {
        assertEquals(HapticPlan.None, plan(sdk = 21, vibrator = false))
        assertEquals(HapticPlan.None, plan(vibrator = false, amplitude = false))
    }
}
