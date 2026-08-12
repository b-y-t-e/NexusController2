package com.nexuscontroller.pad

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The configuration document of PROTOCOL.md §10 — what the phone reports with `0x06 CONFIG`
 * and what the PC pushes back with `0x13 SET_CONFIG`.
 */
class ConfigDocumentTest {

    private val fullDocument = ConfigDocument(
        version = 1,
        type = ControllerType.XBOX360,
        name = "Ania",
        screen = ScreenSize(2400, 1080),
        layout = mapOf(
            "FACE" to LayoutEntry(0.78f, 0.55f, 1.0f, 0f),
            "L_STICK" to LayoutEntry(0.20f, 0.62f, 1.1f, -15f)
        ),
        settings = ConfigSettings(
            haptics = true,
            hapticStrength = 0.85f,
            gyro = false,
            gyroSensitivity = 0.4f,
            touchVibration = true,
            theme = "Dark"
        )
    )

    // ------------------------------------------------------------------ round trip

    @Test
    fun `a full document round trips`() {
        val parsed = ConfigCodec.parse(ConfigCodec.encode(fullDocument))!!
        assertEquals(1, parsed.version)
        assertEquals(ControllerType.XBOX360, parsed.type)
        assertEquals("Ania", parsed.name)
        assertEquals(ScreenSize(2400, 1080), parsed.screen)
        assertEquals(fullDocument.settings, parsed.settings)

        val layout = parsed.layout!!
        assertEquals(setOf("FACE", "L_STICK"), layout.keys)
        assertEquals(0.78f, layout.getValue("FACE").x, 0.0001f)
        assertEquals(0.55f, layout.getValue("FACE").y, 0.0001f)
        assertEquals(1.1f, layout.getValue("L_STICK").scale, 0.0001f)
        assertEquals(-15f, layout.getValue("L_STICK").rotation, 0.0001f)
    }

    @Test
    fun `the pretty form parses to the same thing as the compact one`() {
        val pretty = ConfigCodec.encode(fullDocument, pretty = true)
        assertTrue(pretty.contains('\n'))
        assertEquals(ConfigCodec.parse(ConfigCodec.encode(fullDocument)), ConfigCodec.parse(pretty))
    }

    @Test
    fun `the wire form matches the shape documented in section 10`() {
        val json = ConfigCodec.encode(fullDocument)
        assertTrue(json.startsWith("""{"v":1,"type":"XBOX360","name":"Ania""""))
        assertTrue(json.contains(""""screen":{"w":2400,"h":1080}"""))
        assertTrue(json.contains(""""FACE":{"x":0.78,"y":0.55,"s":1.0,"r":0.0}"""))
        assertTrue(json.contains(""""hapticStrength":0.85"""))
        assertTrue(json.contains(""""theme":"Dark""""))
    }

    @Test
    fun `every device type round trips by name`() {
        listOf(ControllerType.XBOX360, ControllerType.DUALSHOCK4, ControllerType.BUZZ)
            .forEach { type ->
                val encoded = ConfigCodec.encode(ConfigDocument(type = type))
                assertEquals(type, ConfigCodec.parse(encoded)?.type)
            }
    }

    @Test
    fun `a DualShock 3 is described to the PC as a DualShock 4`() {
        // The document describes the *device* the PC emulates. A name it has
        // never heard of would cost the whole document, and there is no DS3
        // device to describe — only a differently labelled phone.
        val encoded = ConfigCodec.encode(ConfigDocument(type = ControllerType.DUALSHOCK3))
        assertTrue(encoded.contains("DUALSHOCK4"))
        assertEquals(ControllerType.DUALSHOCK4, ConfigCodec.parse(encoded)?.type)
    }

    @Test
    fun `the decoder also understands the phone's own face names`() {
        // §10 says a receiver may know further names of its own for faces over
        // the same wire type. The phone does, and reading one back must not be
        // an accident of how the lookup happens to be written.
        val doc = ConfigCodec.parse("""{"v":1,"type":"DUALSHOCK3"}""")
        assertEquals(ControllerType.DUALSHOCK3, doc?.type)
    }

    @Test
    fun `every buzz component id survives a round trip`() {
        val layout = LayoutStore.BUZZ_IDS.associateWith { LayoutEntry(0.5f, 0.5f) }
        val parsed = ConfigCodec.parse(
            ConfigCodec.encode(ConfigDocument(type = ControllerType.BUZZ, layout = layout))
        )!!
        assertEquals(LayoutStore.BUZZ_IDS.toSet(), parsed.layout!!.keys)
    }

    // ------------------------------------------------------------------ merge semantics

    @Test
    fun `a document without layout only carries settings`() {
        val doc = ConfigCodec.parse("""{"v":1,"settings":{"haptics":false}}""")!!
        assertNull("layout must stay absent, not become empty", doc.layout)
        assertEquals(false, doc.settings!!.haptics)
    }

    @Test
    fun `a document without settings only carries layout`() {
        val doc = ConfigCodec.parse("""{"v":1,"layout":{"FACE":{"x":0.5,"y":0.5}}}""")!!
        assertNull("settings must stay absent", doc.settings)
        assertEquals(setOf("FACE"), doc.layout!!.keys)
    }

    @Test
    fun `a present but empty layout is not the same as an absent one`() {
        assertEquals(emptyMap<String, LayoutEntry>(), ConfigCodec.parse("""{"v":1,"layout":{}}""")!!.layout)
        assertNull(ConfigCodec.parse("""{"v":1}""")!!.layout)
    }

    @Test
    fun `merging a layout never wipes components the document did not mention`() {
        val base = LayoutStore.defaults(ControllerType.XBOX360)
        val patch = mapOf("FACE" to LayoutEntry(0.9f, 0.1f, 2f, 30f))
        val merged = ConfigCodec.mergeLayout(base, patch, ControllerType.XBOX360)

        assertEquals(base.keys, merged.keys)
        assertEquals(0.9f, merged.getValue("FACE").x, 0.0001f)
        assertEquals(2f, merged.getValue("FACE").scale, 0.0001f)
        // Untouched components keep exactly what they had.
        assertEquals(base.getValue("DPAD"), merged.getValue("DPAD"))
    }

    @Test
    fun `a null patch leaves the layout completely alone`() {
        val base = LayoutStore.defaults(ControllerType.XBOX360)
        assertEquals(base, ConfigCodec.mergeLayout(base, null, ControllerType.XBOX360))
    }

    @Test
    fun `merging preserves the local key mapping and turbo flag`() {
        // §10 carries only x/y/s/r, so the phone-only extras must not be reset by a push.
        val base = mapOf("FACE" to LayoutEntry(0.1f, 0.1f, 1f, 0f, mappedKey = 65, isTurbo = true))
        val merged = ConfigCodec.mergeLayout(
            base, mapOf("FACE" to LayoutEntry(0.7f, 0.3f)), ControllerType.XBOX360
        )
        assertEquals(0.7f, merged.getValue("FACE").x, 0.0001f)
        assertEquals(65, merged.getValue("FACE").mappedKey)
        assertTrue(merged.getValue("FACE").isTurbo)
    }

    @Test
    fun `merging keeps custom buttons that the PC knows nothing about`() {
        val base = mapOf(
            "FACE" to LayoutEntry(0.1f, 0.1f),
            "BTN_1" to LayoutEntry(0.3f, 0.3f, 1f, 0f, 65, false)
        )
        val merged = ConfigCodec.mergeLayout(
            base, mapOf("FACE" to LayoutEntry(0.8f, 0.8f)), ControllerType.XBOX360
        )
        assertEquals(base.getValue("BTN_1"), merged.getValue("BTN_1"))
    }

    @Test
    fun `a gamepad document cannot pollute a buzz layout`() {
        val base = LayoutStore.defaults(ControllerType.BUZZ)
        val merged = ConfigCodec.mergeLayout(
            base, mapOf("FACE" to LayoutEntry(0.5f, 0.5f)), ControllerType.BUZZ
        )
        assertEquals(base.keys, merged.keys)
        assertFalse(merged.containsKey("FACE"))
    }

    @Test
    fun `settings merge field by field`() {
        val base = ConfigSettings(
            haptics = true, hapticStrength = 0.85f, gyro = false,
            gyroSensitivity = 0.4f, touchVibration = true, theme = "Dark"
        )
        val merged = ConfigSettings(gyro = true, theme = "Neon").mergedOver(base)
        assertEquals(true, merged.gyro)
        assertEquals("Neon", merged.theme)
        // Untouched fields are inherited, not reset.
        assertEquals(true, merged.haptics)
        assertEquals(0.85f, merged.hapticStrength!!, 0.0001f)
        assertEquals(0.4f, merged.gyroSensitivity!!, 0.0001f)
        assertEquals(true, merged.touchVibration)
    }

    @Test
    fun `an empty settings block changes nothing`() {
        val doc = ConfigCodec.parse("""{"v":1,"settings":{}}""")!!
        assertTrue(doc.settings!!.isEmpty)
        val base = ConfigSettings(haptics = true, theme = "Light")
        assertEquals(base, doc.settings!!.mergedOver(base))
    }

    // ------------------------------------------------------------------ tolerance

    @Test
    fun `unknown component ids are dropped`() {
        val doc = ConfigCodec.parse(
            """{"v":1,"layout":{"FACE":{"x":0.5,"y":0.5},"WOBBLE":{"x":0.1,"y":0.1},
               |"BTN_1":{"x":0.2,"y":0.2}}}""".trimMargin()
        )!!
        assertEquals(setOf("FACE"), doc.layout!!.keys)
    }

    @Test
    fun `a component without coordinates is dropped`() {
        val doc = ConfigCodec.parse("""{"v":1,"layout":{"FACE":{"s":2},"PS":{"x":0.5,"y":0.5}}}""")!!
        assertEquals(setOf("PS"), doc.layout!!.keys)
    }

    @Test
    fun `unknown top level keys are ignored, never fatal`() {
        val doc = ConfigCodec.parse(
            """{"v":1,"type":"BUZZ","futureFeature":{"a":[1,2,3]},"nickname":"x",
               |"settings":{"haptics":true,"somethingNew":42}}""".trimMargin()
        )
        assertNotNull(doc)
        assertEquals(ControllerType.BUZZ, doc!!.type)
        assertEquals(true, doc.settings!!.haptics)
    }

    @Test
    fun `an unknown controller type is treated as unspecified`() {
        assertNull(ConfigCodec.parse("""{"v":1,"type":"NINTENDO64"}""")!!.type)
        assertNull(ConfigCodec.parse("""{"v":1,"type":7}""")!!.type)
        assertNull(ConfigCodec.parse("""{"v":1}""")!!.type)
    }

    @Test
    fun `an unknown theme is ignored rather than applied`() {
        assertNull(ConfigCodec.parse("""{"v":1,"settings":{"theme":"Chartreuse"}}""")!!.settings!!.theme)
        assertEquals("Neon", ConfigCodec.parse("""{"v":1,"settings":{"theme":"Neon"}}""")!!.settings!!.theme)
    }

    @Test
    fun `values of the wrong json type are ignored, not fatal`() {
        val doc = ConfigCodec.parse(
            """{"v":1,"name":42,"screen":"big","layout":[1,2],"settings":{"haptics":"yes"}}"""
        )!!
        assertNull(doc.name)
        assertNull(doc.screen)
        assertEquals(emptyMap<String, LayoutEntry>(), doc.layout)
        assertNull(doc.settings!!.haptics)
    }

    @Test
    fun `a nonsensical screen size is ignored`() {
        assertNull(ConfigCodec.parse("""{"v":1,"screen":{"w":0,"h":1080}}""")!!.screen)
        assertNull(ConfigCodec.parse("""{"v":1,"screen":{"w":-2400,"h":-1080}}""")!!.screen)
        assertNull(ConfigCodec.parse("""{"v":1,"screen":{"w":2400}}""")!!.screen)
    }

    // ------------------------------------------------------------------ rejection

    @Test
    fun `a document with an unrecognised schema version is rejected whole`() {
        assertNull(ConfigCodec.parse("""{"v":2,"settings":{"haptics":false}}"""))
        assertNull(ConfigCodec.parse("""{"v":0}"""))
        assertNull(ConfigCodec.parse("""{"v":99}"""))
    }

    @Test
    fun `a document without a schema version is rejected`() {
        assertNull(ConfigCodec.parse("""{"type":"XBOX360"}"""))
        assertNull(ConfigCodec.parse("""{"v":"1"}"""))
    }

    @Test
    fun `malformed json is rejected without throwing`() {
        listOf(
            null, "", "   ", "not json", "{", "}", "[]", "[1,2,3]", "null", "42",
            """{"v":1""", """{"v":}""", """{"v":1} trailing""", """{"v":1,}""",
            """{"v":1,"layout":{"FACE":{"x":}}}"""
        ).forEach { assertNull("should reject: $it", ConfigCodec.parse(it)) }
    }

    // ------------------------------------------------------------------ clamping

    @Test
    fun `out of range coordinates are clamped on the way in`() {
        val doc = ConfigCodec.parse(
            """{"v":1,"layout":{"FACE":{"x":4.5,"y":-2,"s":99,"r":900},
               |"DPAD":{"x":-0.5,"y":0.5,"s":0.01,"r":-900}}}""".trimMargin()
        )!!
        val face = doc.layout!!.getValue("FACE")
        assertEquals(1f, face.x, 0.0001f)
        assertEquals(0f, face.y, 0.0001f)
        assertEquals(3f, face.scale, 0.0001f)
        assertEquals(180f, face.rotation, 0.0001f)

        val dpad = doc.layout!!.getValue("DPAD")
        assertEquals(0f, dpad.x, 0.0001f)
        assertEquals(0.5f, dpad.y, 0.0001f)
        assertEquals(0.5f, dpad.scale, 0.0001f)
        assertEquals(-180f, dpad.rotation, 0.0001f)
    }

    @Test
    fun `out of range coordinates are clamped on the way out too`() {
        val json = ConfigCodec.encode(
            ConfigDocument(layout = mapOf("FACE" to LayoutEntry(4.5f, -2f, 99f, 900f)))
        )
        assertTrue(json.contains(""""FACE":{"x":1.0,"y":0.0,"s":3.0,"r":180.0}"""))
    }

    @Test
    fun `slider settings are clamped to the unit range`() {
        val doc = ConfigCodec.parse(
            """{"v":1,"settings":{"hapticStrength":9,"gyroSensitivity":-4}}"""
        )!!
        assertEquals(1f, doc.settings!!.hapticStrength!!, 0.0001f)
        assertEquals(0f, doc.settings!!.gyroSensitivity!!, 0.0001f)
    }

    @Test
    fun `missing optional component fields fall back to the defaults`() {
        val e = ConfigCodec.parse("""{"v":1,"layout":{"PS":{"x":0.5,"y":0.5}}}""")!!
            .layout!!.getValue("PS")
        assertEquals(1f, e.scale, 0.0001f)
        assertEquals(0f, e.rotation, 0.0001f)
    }
}
