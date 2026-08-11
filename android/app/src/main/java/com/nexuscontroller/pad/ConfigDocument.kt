package com.nexuscontroller.pad

/**
 * The `0x06 CONFIG` / `0x13 SET_CONFIG` document of PROTOCOL.md §10.
 *
 * One JSON document describes everything the user can see and feel on the phone, so a
 * configuration captured from one pad can be pushed onto another straight from the PC
 * dashboard. Pure Kotlin — no Android, no Compose — so every rule below is unit-tested.
 *
 * The single most important rule is **merge, never replace**: a field that is absent from an
 * incoming document means "leave this alone", not "reset it". Every optional field is therefore
 * modelled as a nullable, and `null` is always read as *not mentioned*.
 */

/** Settings half of a §10 document. Every `null` means "the sender did not mention this". */
data class ConfigSettings(
    val haptics: Boolean? = null,
    val hapticStrength: Float? = null,
    val gyro: Boolean? = null,
    val gyroSensitivity: Float? = null,
    val touchVibration: Boolean? = null,
    val theme: String? = null
) {
    val isEmpty: Boolean
        get() = haptics == null && hapticStrength == null && gyro == null &&
            gyroSensitivity == null && touchVibration == null && theme == null

    /** Fields set here win; everything untouched is inherited from [base]. */
    fun mergedOver(base: ConfigSettings): ConfigSettings = ConfigSettings(
        haptics = haptics ?: base.haptics,
        hapticStrength = hapticStrength ?: base.hapticStrength,
        gyro = gyro ?: base.gyro,
        gyroSensitivity = gyroSensitivity ?: base.gyroSensitivity,
        touchVibration = touchVibration ?: base.touchVibration,
        theme = theme ?: base.theme
    )
}

/**
 * A parsed §10 document.
 *
 * [layout] and [settings] are null when the key was absent, which is what drives the merge
 * semantics — an empty (but present) `layout` object is *not* the same thing as no `layout`.
 */
data class ConfigDocument(
    val version: Int = ConfigCodec.SCHEMA_VERSION,
    val type: ControllerType? = null,
    val name: String? = null,
    val screen: ScreenSize? = null,
    val layout: Map<String, LayoutEntry>? = null,
    val settings: ConfigSettings? = null
)

object ConfigCodec {

    const val SCHEMA_VERSION = 1

    /** The three themes the app actually renders; anything else is ignored, never applied. */
    val THEMES = listOf("Dark", "Neon", "Light")

    // ------------------------------------------------------------------ writing

    /**
     * Serialises [doc]. Only the §10 component IDs are emitted — the phone's own `BTN_*`
     * extras have no meaning on the PC — and every value is clamped on the way out.
     */
    fun encode(doc: ConfigDocument, pretty: Boolean = false): String {
        val root = LinkedHashMap<String, Any?>()
        root["v"] = doc.version
        doc.type?.let { root["type"] = it.name }
        doc.name?.let { root["name"] = it }
        doc.screen?.let {
            root["screen"] = linkedMapOf<String, Any?>("w" to it.width, "h" to it.height)
        }
        doc.layout?.let { layout ->
            val out = LinkedHashMap<String, Any?>()
            for (id in ComponentSizes.IDS) {
                val e = layout[id] ?: continue
                val c = LayoutBounds.clamp(e)
                out[id] = linkedMapOf<String, Any?>(
                    "x" to c.x, "y" to c.y, "s" to c.scale, "r" to c.rotation
                )
            }
            root["layout"] = out
        }
        doc.settings?.let { s ->
            val out = LinkedHashMap<String, Any?>()
            s.haptics?.let { out["haptics"] = it }
            s.hapticStrength?.let { out["hapticStrength"] = unitRange(it) }
            s.gyro?.let { out["gyro"] = it }
            s.gyroSensitivity?.let { out["gyroSensitivity"] = unitRange(it) }
            s.touchVibration?.let { out["touchVibration"] = it }
            s.theme?.let { theme -> THEMES.firstOrNull { it == theme }?.let { out["theme"] = it } }
            root["settings"] = out
        }
        return MiniJson.write(root, if (pretty) 2 else -1)
    }

    // ------------------------------------------------------------------ reading

    /**
     * Parses a §10 document.
     *
     * Returns null — meaning "ignore this entirely" — only for malformed JSON, a non-object
     * root, or a schema version we do not speak. Everything else is tolerated: unknown
     * top-level keys are skipped, unknown component IDs are dropped, a component missing `x`
     * or `y` is dropped, and out-of-range numbers are clamped.
     */
    fun parse(json: String?): ConfigDocument? {
        if (json.isNullOrBlank()) return null
        val root = try {
            MiniJson.parseObject(json)
        } catch (e: Exception) {
            return null
        }

        // `v` must be present and recognised: §10 says a peer that does not know the version
        // ignores the document rather than guessing at its meaning.
        val version = (root["v"] as? Double)?.toInt() ?: return null
        if (version != SCHEMA_VERSION) return null

        return ConfigDocument(
            version = version,
            type = (root["type"] as? String)?.let { name ->
                ControllerType.entries.firstOrNull { it.name.equals(name, ignoreCase = true) }
            },
            name = root["name"] as? String,
            screen = parseScreen(root["screen"]),
            layout = if (root.containsKey("layout")) parseLayout(root["layout"]) else null,
            settings = if (root.containsKey("settings")) parseSettings(root["settings"]) else null
        )
    }

    private fun parseScreen(value: Any?): ScreenSize? {
        @Suppress("UNCHECKED_CAST")
        val obj = value as? Map<String, Any?> ?: return null
        val w = (obj["w"] as? Double)?.toInt() ?: return null
        val h = (obj["h"] as? Double)?.toInt() ?: return null
        if (w <= 0 || h <= 0) return null
        return ScreenSize(w, h)
    }

    /** An absent `layout` is null (leave it alone); a present but useless one is an empty map. */
    private fun parseLayout(value: Any?): Map<String, LayoutEntry> {
        @Suppress("UNCHECKED_CAST")
        val obj = value as? Map<String, Any?> ?: return emptyMap()
        val out = LinkedHashMap<String, LayoutEntry>()
        for ((id, raw) in obj) {
            if (!ComponentSizes.isKnown(id)) continue    // §10: unknown IDs are dropped
            @Suppress("UNCHECKED_CAST")
            val e = raw as? Map<String, Any?> ?: continue
            val x = (e["x"] as? Double)?.toFloat() ?: continue
            val y = (e["y"] as? Double)?.toFloat() ?: continue
            out[id] = LayoutBounds.clamp(
                LayoutEntry(
                    x = x,
                    y = y,
                    scale = (e["s"] as? Double)?.toFloat() ?: 1f,
                    rotation = (e["r"] as? Double)?.toFloat() ?: 0f
                )
            )
        }
        return out
    }

    private fun parseSettings(value: Any?): ConfigSettings {
        @Suppress("UNCHECKED_CAST")
        val obj = value as? Map<String, Any?> ?: return ConfigSettings()
        val theme = obj["theme"] as? String
        return ConfigSettings(
            haptics = obj["haptics"] as? Boolean,
            hapticStrength = (obj["hapticStrength"] as? Double)?.toFloat()?.let { unitRange(it) },
            gyro = obj["gyro"] as? Boolean,
            gyroSensitivity = (obj["gyroSensitivity"] as? Double)?.toFloat()?.let { unitRange(it) },
            touchVibration = obj["touchVibration"] as? Boolean,
            theme = THEMES.firstOrNull { it.equals(theme, ignoreCase = true) }
        )
    }

    // ------------------------------------------------------------------ merging

    /**
     * Applies the `layout` of an incoming document on top of what the phone already has.
     *
     * A component the document does not mention keeps its current placement, and — because
     * §10 carries only `x/y/s/r` — its local key mapping and turbo flag survive too.
     * Components that are not part of [type]'s ID set are ignored, so a gamepad document
     * pushed while the phone is in Buzz mode cannot pollute the Buzz layout.
     */
    fun mergeLayout(
        base: Map<String, LayoutEntry>,
        patch: Map<String, LayoutEntry>?,
        type: ControllerType
    ): Map<String, LayoutEntry> {
        val out = LinkedHashMap(LayoutBounds.clampAll(base))
        if (patch == null) return out
        val allowed = if (type.isGamepad) LayoutStore.GAMEPAD_IDS.toSet() else LayoutStore.BUZZ_IDS.toSet()
        for ((id, entry) in patch) {
            if (id !in allowed) continue
            val existing = out[id]
            val clamped = LayoutBounds.clamp(entry)
            out[id] = clamped.copy(
                mappedKey = existing?.mappedKey ?: 0,
                isTurbo = existing?.isTurbo ?: false
            )
        }
        return out
    }

    private fun unitRange(v: Float): Float = if (v.isFinite()) v.coerceIn(0f, 1f) else 0f
}
