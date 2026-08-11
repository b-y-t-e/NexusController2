package com.nexuscontroller.pad

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LayoutSerializationTest {

    private val layout = mapOf(
        "FACE" to LayoutEntry(0.78f, 0.55f, 1.4f, 45f, 0, true),
        "L_STICK" to LayoutEntry(0.2f, 0.62f, 0.75f, -30.5f, 0, false),
        "BTN_1" to LayoutEntry(0.1f, 0.2f, 1f, 0f, 65, false)
    )

    @Test
    fun `layout round trips through json`() {
        val decoded = LayoutSerializer.decode(LayoutSerializer.encode(layout))
        assertEquals(layout.keys, decoded.keys)
        layout.forEach { (id, entry) ->
            val other = decoded.getValue(id)
            assertEquals(entry.x, other.x, 0.001f)
            assertEquals(entry.y, other.y, 0.001f)
            assertEquals(entry.scale, other.scale, 0.001f)
            assertEquals(entry.rotation, other.rotation, 0.001f)
            assertEquals(entry.mappedKey, other.mappedKey)
            assertEquals(entry.isTurbo, other.isTurbo)
        }
    }

    @Test
    fun `rotation survives the round trip`() {
        val encoded = LayoutSerializer.encode(mapOf("DPAD" to LayoutEntry(0f, 0f, 1f, 90f)))
        assertTrue(encoded.contains("\"r\":90.0"))
        assertEquals(90f, LayoutSerializer.decode(encoded).getValue("DPAD").rotation, 0.001f)
    }

    @Test
    fun `buzz components round trip under their own ids`() {
        val buzz = LayoutStore.BUZZ_IDS.associateWith { LayoutEntry(0.5f, 0.75f) }
        val decoded = LayoutSerializer.decode(LayoutSerializer.encode(buzz))
        assertEquals(LayoutStore.BUZZ_IDS.toSet(), decoded.keys)
    }

    @Test
    fun `legacy v1 payloads still load`() {
        val legacy = """{"FACE":{"x":100,"y":200,"s":1.5,"r":0,"k":0,"turbo":false}}"""
        val decoded = LayoutSerializer.decode(legacy)
        assertEquals(100f, decoded.getValue("FACE").x, 0.001f)
        assertEquals(1.5f, decoded.getValue("FACE").scale, 0.001f)
    }

    @Test
    fun `missing optional fields fall back to defaults`() {
        val decoded = LayoutSerializer.decode("""{"PS":{"x":5,"y":6}}""")
        val e = decoded.getValue("PS")
        assertEquals(1f, e.scale, 0.001f)
        assertEquals(0f, e.rotation, 0.001f)
        assertEquals(0, e.mappedKey)
        assertEquals(false, e.isTurbo)
    }

    @Test
    fun `entries without coordinates are skipped`() {
        assertTrue(LayoutSerializer.decode("""{"PS":{"s":2}}""").isEmpty())
    }

    @Test
    fun `malformed json decodes to an empty layout`() {
        listOf(null, "", "   ", "not json", "{", "[1,2,3]", """{"PS":}""", """{"PS":{"x":1,"y":2}} trailing""")
            .forEach { assertTrue("should be empty for: $it", LayoutSerializer.decode(it).isEmpty()) }
    }

    @Test
    fun `empty layout round trips`() {
        assertEquals("{}", LayoutSerializer.encode(emptyMap()))
        assertTrue(LayoutSerializer.decode("{}").isEmpty())
    }

    @Test
    fun `layouts are keyed per profile and controller family`() {
        assertEquals("layout_json_autosave", LayoutSerializer.prefsKey("autosave", ControllerType.XBOX360))
        // Xbox and DS4 share the gamepad layout: same components, different glyphs.
        assertEquals(
            LayoutSerializer.prefsKey("autosave", ControllerType.XBOX360),
            LayoutSerializer.prefsKey("autosave", ControllerType.DUALSHOCK4)
        )
        // Buzz must never overwrite a gamepad layout.
        assertEquals("layout_json_autosave_buzz", LayoutSerializer.prefsKey("autosave", ControllerType.BUZZ))
        assertNotEquals(
            LayoutSerializer.prefsKey("racing", ControllerType.XBOX360),
            LayoutSerializer.prefsKey("racing", ControllerType.BUZZ)
        )
        assertNotEquals(
            LayoutSerializer.prefsKey("a", ControllerType.XBOX360),
            LayoutSerializer.prefsKey("b", ControllerType.XBOX360)
        )
    }

    @Test
    fun `encode clamps every value into the protocol range`() {
        val encoded = LayoutSerializer.encode(
            mapOf("FACE" to LayoutEntry(4f, -2f, 99f, 900f))
        )
        val e = LayoutSerializer.decode(encoded).getValue("FACE")
        assertEquals(1f, e.x, 0.001f)
        assertEquals(0f, e.y, 0.001f)
        assertEquals(LayoutBounds.MAX_SCALE, e.scale, 0.001f)
        assertEquals(LayoutBounds.MAX_ROTATION, e.rotation, 0.001f)
    }

    @Test
    fun `decode leaves legacy pixels alone so the migration can spot them`() {
        // Clamping here would erase the evidence that these are pixels, not fractions.
        val decoded = LayoutSerializer.decode("""{"FACE":{"x":1320,"y":648}}""")
        assertEquals(1320f, decoded.getValue("FACE").x, 0.001f)
    }

    @Test
    fun `defaults are normalised and screen independent`() {
        ControllerType.entries.forEach { type ->
            LayoutStore.defaults(type).forEach { (id, e) ->
                assertTrue("$id x out of range: ${e.x}", e.x in 0f..1f)
                assertTrue("$id y out of range: ${e.y}", e.y in 0f..1f)
                assertTrue("$id scale out of range", e.scale in LayoutBounds.MIN_SCALE..LayoutBounds.MAX_SCALE)
            }
        }
    }

    @Test
    fun `defaults cover every component of each type`() {
        val gamepad = LayoutStore.defaults(ControllerType.XBOX360)
        assertEquals(LayoutStore.GAMEPAD_IDS.toSet(), gamepad.keys)
        val ds4 = LayoutStore.defaults(ControllerType.DUALSHOCK4)
        assertEquals(gamepad.keys, ds4.keys)
        val buzz = LayoutStore.defaults(ControllerType.BUZZ)
        assertEquals(LayoutStore.BUZZ_IDS.toSet(), buzz.keys)
        // No Buzz component may collide with a gamepad component id.
        assertTrue(buzz.keys.intersect(gamepad.keys).isEmpty())
    }

    @Test
    fun `keys with special characters survive`() {
        val encoded = LayoutSerializer.encode(mapOf("BTN_\"q\"" to LayoutEntry(0.5f, 0.5f)))
        assertEquals(setOf("BTN_\"q\""), LayoutSerializer.decode(encoded).keys)
    }
}
