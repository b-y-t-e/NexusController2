package com.nexuscontroller.pad

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * PROTOCOL.md §10 moved layouts from absolute pixels to normalised centres. Users have saved
 * layouts in the old form, so the conversion has to be right and it has to be idempotent.
 */
class LayoutMigrationTest {

    private val screen = ScreenSize(2400, 1080)

    // ------------------------------------------------------------------ detection

    @Test
    fun `pixel layouts are recognised and fractional ones are not`() {
        assertTrue(LayoutMigration.looksLikePixels(mapOf("FACE" to LayoutEntry(1320f, 648f))))
        assertTrue(LayoutMigration.looksLikePixels(mapOf("PS" to LayoutEntry(0.5f, 400f))))
        assertFalse(LayoutMigration.looksLikePixels(mapOf("FACE" to LayoutEntry(0.62f, 0.75f))))
        assertFalse(LayoutMigration.looksLikePixels(emptyMap()))
    }

    @Test
    fun `a value just above one is still treated as a fraction`() {
        // Rounding noise around the clamp must not be mistaken for a pixel coordinate.
        assertFalse(LayoutMigration.looksLikePixels(mapOf("PS" to LayoutEntry(1.0001f, 1f))))
    }

    // ------------------------------------------------------------------ conversion

    @Test
    fun `a legacy pixel layout converts to the centre of the component`() {
        // FACE is nominally 0.30 of the screen height, i.e. 324 px, so its centre sits 162 px
        // past the stored top-left corner.
        val migrated = LayoutMigration.normalise(
            mapOf("FACE" to LayoutEntry(1320f, 648f)), screen
        )
        val e = migrated.getValue("FACE")
        assertEquals((1320f + 162f) / 2400f, e.x, 0.0001f)
        assertEquals((648f + 162f) / 1080f, e.y, 0.0001f)
    }

    @Test
    fun `each component uses its own nominal size`() {
        val migrated = LayoutMigration.normalise(
            mapOf(
                "L_STICK" to LayoutEntry(0f, 0f),   // nominal 0.34 -> half 183.6 px
                "SHARE" to LayoutEntry(0f, 0f),     // nominal 0.09 -> half  48.6 px
                "FACE" to LayoutEntry(1320f, 648f)  // marks the document as legacy pixels
            ),
            screen
        )
        assertEquals(183.6f / 2400f, migrated.getValue("L_STICK").x, 0.0001f)
        assertEquals(48.6f / 2400f, migrated.getValue("SHARE").x, 0.0001f)
    }

    @Test
    fun `a whole legacy gamepad layout lands on screen`() {
        val legacy = mapOf(
            "L1" to LayoutEntry(192f, 129.6f, 0.9f),
            "L2" to LayoutEntry(192f, 270f, 0.9f),
            "R1" to LayoutEntry(1968f, 129.6f, 0.9f),
            "L_STICK" to LayoutEntry(192f, 702f, 1.2f),
            "FACE" to LayoutEntry(1320f, 648f)
        )
        val migrated = LayoutMigration.normalise(legacy, screen)
        assertEquals(legacy.keys, migrated.keys)
        migrated.forEach { (id, e) ->
            assertTrue("$id x = ${e.x}", e.x in 0f..1f)
            assertTrue("$id y = ${e.y}", e.y in 0f..1f)
        }
        // The right-hand bumper stays on the right-hand side.
        assertTrue(migrated.getValue("R1").x > 0.8f)
        assertTrue(migrated.getValue("L1").x < 0.2f)
        // Scale and the local extras survive the conversion untouched.
        assertEquals(1.2f, migrated.getValue("L_STICK").scale, 0.001f)
    }

    @Test
    fun `an unknown component falls back to the default nominal size`() {
        val migrated = LayoutMigration.normalise(
            mapOf("BTN_1" to LayoutEntry(0f, 0f), "FACE" to LayoutEntry(1320f, 648f)),
            screen
        )
        val half = ComponentSizes.DEFAULT_NOMINAL * 1080f / 2f
        assertEquals(half / 2400f, migrated.getValue("BTN_1").x, 0.0001f)
    }

    @Test
    fun `a component parked off the right edge is clamped, not lost`() {
        val migrated = LayoutMigration.normalise(mapOf("FACE" to LayoutEntry(9000f, 9000f)), screen)
        assertEquals(1f, migrated.getValue("FACE").x, 0.0001f)
        assertEquals(1f, migrated.getValue("FACE").y, 0.0001f)
    }

    // ------------------------------------------------------------------ idempotence

    @Test
    fun `an already normalised layout is left alone`() {
        val normalised = mapOf(
            "FACE" to LayoutEntry(0.78f, 0.55f, 1.0f, 0f),
            "L_STICK" to LayoutEntry(0.20f, 0.62f, 1.1f, -15f, 65, true)
        )
        val migrated = LayoutMigration.normalise(normalised, screen)
        assertEquals(normalised, migrated)
    }

    @Test
    fun `normalising twice is a fixed point`() {
        val legacy = mapOf(
            "FACE" to LayoutEntry(1320f, 648f, 1.4f, 45f),
            "DPAD" to LayoutEntry(720f, 648f),
            "R_STICK" to LayoutEntry(1920f, 702f, 1.2f)
        )
        val once = LayoutMigration.normalise(legacy, screen)
        val twice = LayoutMigration.normalise(once, screen)
        assertEquals(once, twice)
    }

    @Test
    fun `a migrated layout survives a storage round trip unchanged`() {
        val legacy = mapOf("FACE" to LayoutEntry(1320f, 648f, 1.4f, 45f, 0, true))
        val once = LayoutMigration.normalise(legacy, screen)
        val reloaded = LayoutSerializer.decodeNormalised(LayoutSerializer.encode(once), screen)
        assertEquals(once.keys, reloaded.keys)
        assertEquals(once.getValue("FACE").x, reloaded.getValue("FACE").x, 0.0002f)
        assertEquals(once.getValue("FACE").y, reloaded.getValue("FACE").y, 0.0002f)
        assertTrue(reloaded.getValue("FACE").isTurbo)
    }

    @Test
    fun `a mixed document is migrated as a whole`() {
        // One pixel value gives the game away; treating the rest as fractions would scatter
        // the layout, so the whole document is read as legacy.
        val migrated = LayoutMigration.normalise(
            mapOf("FACE" to LayoutEntry(1320f, 648f), "PS" to LayoutEntry(0f, 0f)),
            screen
        )
        assertTrue(migrated.getValue("FACE").x > 0.5f)
        assertTrue(migrated.getValue("PS").x > 0f)
    }

    @Test
    fun `an empty layout migrates to an empty layout`() {
        assertTrue(LayoutMigration.normalise(emptyMap(), screen).isEmpty())
    }

    @Test
    fun `a degenerate screen size cannot produce infinities`() {
        val migrated = LayoutMigration.normalise(
            mapOf("FACE" to LayoutEntry(100f, 100f)), ScreenSize(0, 0)
        )
        val e = migrated.getValue("FACE")
        assertTrue(e.x.isFinite() && e.y.isFinite())
        assertTrue(e.x in 0f..1f && e.y in 0f..1f)
    }

    // ------------------------------------------------------------------ size table

    @Test
    fun `the nominal size table matches PROTOCOL section 10`() {
        assertEquals(0.34f, ComponentSizes.nominal("L_STICK"), 0.0001f)
        assertEquals(0.34f, ComponentSizes.nominal("R_STICK"), 0.0001f)
        assertEquals(0.30f, ComponentSizes.nominal("DPAD"), 0.0001f)
        assertEquals(0.30f, ComponentSizes.nominal("FACE"), 0.0001f)
        assertEquals(0.13f, ComponentSizes.nominal("L1"), 0.0001f)
        assertEquals(0.13f, ComponentSizes.nominal("R1"), 0.0001f)
        assertEquals(0.15f, ComponentSizes.nominal("L2"), 0.0001f)
        assertEquals(0.15f, ComponentSizes.nominal("R2"), 0.0001f)
        assertEquals(0.09f, ComponentSizes.nominal("SHARE"), 0.0001f)
        assertEquals(0.09f, ComponentSizes.nominal("OPTIONS"), 0.0001f)
        assertEquals(0.10f, ComponentSizes.nominal("PS"), 0.0001f)
        assertEquals(0.38f, ComponentSizes.nominal("BUZZ_RED"), 0.0001f)
        listOf("BUZZ_BLUE", "BUZZ_ORANGE", "BUZZ_GREEN", "BUZZ_YELLOW").forEach {
            assertEquals(0.16f, ComponentSizes.nominal(it), 0.0001f)
        }
    }

    @Test
    fun `the size table covers exactly the documented component ids`() {
        val expected = (LayoutStore.GAMEPAD_IDS + LayoutStore.BUZZ_IDS).toSet()
        assertEquals(expected, ComponentSizes.IDS)
        assertFalse(ComponentSizes.isKnown("BTN_1"))
        assertFalse(ComponentSizes.isKnown("NOPE"))
    }

    @Test
    fun `footprint scales with the component scale and the screen`() {
        assertEquals(324f, ComponentSizes.footprintPx("FACE", 1f, 1080f), 0.01f)
        assertEquals(648f, ComponentSizes.footprintPx("FACE", 2f, 1080f), 0.01f)
        // The protocol range applies here too: 99x is not a legal scale.
        assertEquals(972f, ComponentSizes.footprintPx("FACE", 99f, 1080f), 0.01f)
    }

    // ------------------------------------------------------------------ bounds

    @Test
    fun `bounds clamp x y scale and rotation`() {
        val e = LayoutBounds.clamp(LayoutEntry(-3f, 7f, 0.01f, -900f, 65, true))
        assertEquals(0f, e.x, 0.0001f)
        assertEquals(1f, e.y, 0.0001f)
        assertEquals(0.5f, e.scale, 0.0001f)
        assertEquals(-180f, e.rotation, 0.0001f)
        // Local-only fields are never touched by clamping.
        assertEquals(65, e.mappedKey)
        assertTrue(e.isTurbo)
    }

    @Test
    fun `non finite values degrade to something usable`() {
        val e = LayoutBounds.clamp(
            LayoutEntry(Float.NaN, Float.POSITIVE_INFINITY, Float.NaN, Float.NEGATIVE_INFINITY)
        )
        assertEquals(0f, e.x, 0.0001f)
        assertEquals(0f, e.y, 0.0001f)
        assertEquals(1f, e.scale, 0.0001f)
        assertEquals(0f, e.rotation, 0.0001f)
    }
}
