package com.nexuscontroller.pad

/**
 * Serialisation of on-screen layouts.
 *
 * Kept free of Android and Compose types so it is unit-testable; [CompConfig] holds the
 * same fields but as observable Compose state, and converts through [LayoutEntry].
 *
 * **Coordinates are normalised** (PROTOCOL.md §10): `x` and `y` are fractions of the usable
 * screen in `0.0`–`1.0` and address the *centre* of the component, never a pixel and never a
 * corner. That is the only way a layout authored on the PC can land in the same place on a
 * phone with a different screen. Layouts written by older builds stored raw pixels; they are
 * converted on load by [LayoutMigration].
 *
 * Stored shape: `{"FACE":{"x":0.78,"y":0.55,"s":1.0,"r":0.0,"k":0,"turbo":false}}`
 */
data class LayoutEntry(
    val x: Float,
    val y: Float,
    val scale: Float = 1f,
    val rotation: Float = 0f,
    val mappedKey: Int = 0,
    val isTurbo: Boolean = false
)

/** Usable screen in pixels. Only ever informational on the wire, but it drives the migration. */
data class ScreenSize(val width: Int, val height: Int) {
    val wf: Float get() = width.toFloat().coerceAtLeast(1f)
    val hf: Float get() = height.toFloat().coerceAtLeast(1f)

    companion object {
        /** Something plausible for a landscape phone, used before the window has been measured. */
        val FALLBACK = ScreenSize(2400, 1080)
    }
}

/**
 * The ranges PROTOCOL.md §10 mandates. Applied on **both** read and write so neither a
 * corrupt preference file nor a buggy PC editor can push a component off the screen or
 * blow a component up to fill it.
 */
object LayoutBounds {
    const val MIN_SCALE = 0.5f
    const val MAX_SCALE = 3.0f
    const val MAX_ROTATION = 180f

    fun position(v: Float): Float = if (v.isFinite()) v.coerceIn(0f, 1f) else 0f
    fun scale(v: Float): Float = if (v.isFinite()) v.coerceIn(MIN_SCALE, MAX_SCALE) else 1f
    fun rotation(v: Float): Float = if (v.isFinite()) v.coerceIn(-MAX_ROTATION, MAX_ROTATION) else 0f

    fun clamp(e: LayoutEntry): LayoutEntry = LayoutEntry(
        x = position(e.x),
        y = position(e.y),
        scale = scale(e.scale),
        rotation = rotation(e.rotation),
        mappedKey = e.mappedKey,
        isTurbo = e.isTurbo
    )

    fun clampAll(entries: Map<String, LayoutEntry>): Map<String, LayoutEntry> =
        entries.mapValuesTo(LinkedHashMap()) { clamp(it.value) }
}

/**
 * Nominal component footprints from PROTOCOL.md §10, expressed as a fraction of screen
 * **height** at `s = 1.0`. This is the shared contract between the phone and the PC preview:
 * both sides answer "how big is a `DPAD`?" from this one table, so a layout dragged around in
 * the PC editor lands where the user aimed it.
 */
object ComponentSizes {

    /** Custom `BTN_*` buttons are a phone-only extension and are not in §10. */
    const val DEFAULT_NOMINAL = 0.10f

    private val NOMINAL = linkedMapOf(
        "L_STICK" to 0.34f,
        "R_STICK" to 0.34f,
        "DPAD" to 0.30f,
        "FACE" to 0.30f,
        "L1" to 0.13f,
        "R1" to 0.13f,
        "L2" to 0.15f,
        "R2" to 0.15f,
        "SHARE" to 0.09f,
        "OPTIONS" to 0.09f,
        "PS" to 0.10f,
        "BUZZ_RED" to 0.38f,
        "BUZZ_BLUE" to 0.16f,
        "BUZZ_ORANGE" to 0.16f,
        "BUZZ_GREEN" to 0.16f,
        "BUZZ_YELLOW" to 0.16f
    )

    /** Every component ID PROTOCOL.md §10 defines. Anything else is dropped from a document. */
    val IDS: Set<String> = NOMINAL.keys

    fun isKnown(id: String): Boolean = id in NOMINAL

    fun nominal(id: String): Float = NOMINAL[id] ?: DEFAULT_NOMINAL

    /** Footprint in pixels at `s = 1.0` — the size used to convert a corner to a centre. */
    fun nominalPx(id: String, screenHeightPx: Float): Float = nominal(id) * screenHeightPx

    /** Footprint in pixels as actually drawn, i.e. including the component's own scale. */
    fun footprintPx(id: String, scale: Float, screenHeightPx: Float): Float =
        nominal(id) * LayoutBounds.scale(scale) * screenHeightPx
}

/**
 * Converts layouts written before normalised coordinates existed.
 *
 * Builds up to and including protocol v1 stored `x`/`y` as absolute pixels addressing the
 * component's top-left corner. Anything past [PIXEL_THRESHOLD] cannot be a fraction of the
 * screen, so a document containing such a value is read as the legacy form and converted with
 * the current screen size; saved layouts are migrated, never dropped.
 */
object LayoutMigration {

    /** Legal normalised values stop at 1.0; a little slack keeps rounding noise out of it. */
    const val PIXEL_THRESHOLD = 1.5f

    fun looksLikePixels(entries: Map<String, LayoutEntry>): Boolean =
        entries.values.any { it.x > PIXEL_THRESHOLD || it.y > PIXEL_THRESHOLD }

    /**
     * Returns [entries] in normalised form: converted if they are legacy pixels, otherwise
     * only clamped. Already-normalised layouts are a fixed point of this function.
     */
    fun normalise(entries: Map<String, LayoutEntry>, screen: ScreenSize): Map<String, LayoutEntry> {
        if (entries.isEmpty()) return emptyMap()
        if (!looksLikePixels(entries)) return LayoutBounds.clampAll(entries)
        return entries.mapValuesTo(LinkedHashMap()) { (id, e) ->
            // Legacy x/y were the top-left corner of an unscaled box, so the centre sits half a
            // nominal footprint away. `s` only ever scaled the drawing, never the box.
            val half = ComponentSizes.nominalPx(id, screen.hf) / 2f
            LayoutBounds.clamp(
                e.copy(x = (e.x + half) / screen.wf, y = (e.y + half) / screen.hf)
            )
        }
    }
}

object LayoutSerializer {

    /**
     * Preference key for a profile. Xbox and DualShock share one gamepad layout (the
     * components are identical, only the glyphs differ), Buzz gets its own so switching
     * controller type never scrambles a saved gamepad layout.
     */
    fun prefsKey(profile: String, type: ControllerType): String =
        if (type.isGamepad) "layout_json_$profile" else "layout_json_${profile}_buzz"

    /** Writes the normalised form; values are clamped so nothing out of range is ever stored. */
    fun encode(configs: Map<String, LayoutEntry>): String {
        val sb = StringBuilder("{")
        var first = true
        for ((key, raw) in configs) {
            val e = LayoutBounds.clamp(raw)
            if (!first) sb.append(',')
            first = false
            sb.append(MiniJson.quote(key)).append(":{")
                .append("\"x\":").append(MiniJson.number(e.x)).append(',')
                .append("\"y\":").append(MiniJson.number(e.y)).append(',')
                .append("\"s\":").append(MiniJson.number(e.scale)).append(',')
                .append("\"r\":").append(MiniJson.number(e.rotation)).append(',')
                .append("\"k\":").append(e.mappedKey).append(',')
                .append("\"turbo\":").append(e.isTurbo)
                .append('}')
        }
        return sb.append('}').toString()
    }

    /**
     * Reads the raw stored values **without** clamping — [LayoutMigration] has to be able to
     * see legacy pixel coordinates for what they are. Returns an empty map when the payload is
     * missing or unparseable.
     */
    fun decode(json: String?): Map<String, LayoutEntry> {
        if (json.isNullOrBlank()) return emptyMap()
        val root = try {
            MiniJson.parseObject(json)
        } catch (e: Exception) {
            return emptyMap()
        }
        val out = LinkedHashMap<String, LayoutEntry>()
        for ((key, value) in root) {
            @Suppress("UNCHECKED_CAST")
            val obj = value as? Map<String, Any?> ?: continue
            val x = obj.numberOr("x", null) ?: continue
            val y = obj.numberOr("y", null) ?: continue
            out[key] = LayoutEntry(
                x = x,
                y = y,
                scale = obj.numberOr("s", 1f)!!,
                rotation = obj.numberOr("r", 0f)!!,
                mappedKey = obj.numberOr("k", 0f)!!.toInt(),
                isTurbo = obj["turbo"] as? Boolean ?: false
            )
        }
        return out
    }

    /** Convenience: read a stored payload straight into the normalised, clamped form. */
    fun decodeNormalised(json: String?, screen: ScreenSize): Map<String, LayoutEntry> =
        LayoutMigration.normalise(decode(json), screen)

    private fun Map<String, Any?>.numberOr(key: String, fallback: Float?): Float? =
        (this[key] as? Double)?.toFloat() ?: fallback
}

/**
 * Minimal JSON reader for the flat layout documents above. Deliberately tiny: it exists so
 * layout persistence has no `org.json` (i.e. no Android) dependency and can be tested on
 * the JVM. Objects become `Map<String, Any?>`, numbers become `Double`.
 */
internal object MiniJson {

    /**
     * Writes a value tree back out. [indent] `< 0` emits the compact form that goes on the
     * wire; `>= 0` pretty-prints, which is what the debug dumps and the docs use.
     * `Int`/`Long` are written as integers, `Float`/`Double` as decimals.
     */
    fun write(value: Any?, indent: Int = -1): String =
        StringBuilder().also { append(it, value, indent, 0) }.toString()

    private fun append(sb: StringBuilder, value: Any?, indent: Int, depth: Int) {
        when (value) {
            null -> sb.append("null")
            is Boolean -> sb.append(value)
            is Int, is Long -> sb.append(value)
            is Float -> sb.append(number(value))
            is Double -> sb.append(number(value.toFloat()))
            is String -> sb.append(quote(value))
            is Map<*, *> -> {
                if (value.isEmpty()) { sb.append("{}"); return }
                sb.append('{')
                var first = true
                for ((k, v) in value) {
                    if (!first) sb.append(',')
                    first = false
                    newline(sb, indent, depth + 1)
                    sb.append(quote(k.toString())).append(':')
                    if (indent >= 0) sb.append(' ')
                    append(sb, v, indent, depth + 1)
                }
                newline(sb, indent, depth)
                sb.append('}')
            }
            is List<*> -> {
                if (value.isEmpty()) { sb.append("[]"); return }
                sb.append('[')
                var first = true
                for (v in value) {
                    if (!first) sb.append(',')
                    first = false
                    newline(sb, indent, depth + 1)
                    append(sb, v, indent, depth + 1)
                }
                newline(sb, indent, depth)
                sb.append(']')
            }
            else -> sb.append(quote(value.toString()))
        }
    }

    private fun newline(sb: StringBuilder, indent: Int, depth: Int) {
        if (indent < 0) return
        sb.append('\n')
        repeat(indent * depth) { sb.append(' ') }
    }

    /**
     * Floats rounded to four decimals, so `0.78f` serialises as `0.78` and not as
     * `0.7799999713897705`. Non-finite values degrade to `0.0` rather than to invalid JSON.
     */
    fun number(v: Float): String {
        if (!v.isFinite()) return "0.0"
        val rounded = Math.round(v.toDouble() * 10000.0) / 10000.0
        return if (rounded == Math.floor(rounded) && Math.abs(rounded) < 1e15) {
            "${rounded.toLong()}.0"
        } else {
            rounded.toString()
        }
    }

    fun quote(s: String): String {
        val sb = StringBuilder("\"")
        for (c in s) {
            when (c) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> if (c < ' ') sb.append("\\u%04x".format(c.code)) else sb.append(c)
            }
        }
        return sb.append('"').toString()
    }

    fun parseObject(text: String): Map<String, Any?> {
        val p = Parser(text)
        p.skipWs()
        val value = p.readValue()
        p.skipWs()
        require(p.atEnd()) { "trailing content at ${p.pos}" }
        @Suppress("UNCHECKED_CAST")
        return value as? Map<String, Any?> ?: throw IllegalArgumentException("root is not an object")
    }

    private class Parser(private val s: String) {
        var pos = 0

        fun atEnd() = pos >= s.length

        fun skipWs() {
            while (pos < s.length && s[pos].isWhitespace()) pos++
        }

        fun readValue(): Any? {
            skipWs()
            require(pos < s.length) { "unexpected end of input" }
            return when (val c = s[pos]) {
                '{' -> readObject()
                '[' -> readArray()
                '"' -> readString()
                't' -> readLiteral("true", true)
                'f' -> readLiteral("false", false)
                'n' -> readLiteral("null", null)
                else -> if (c == '-' || c.isDigit()) readNumber()
                else throw IllegalArgumentException("unexpected '$c' at $pos")
            }
        }

        private fun readObject(): Map<String, Any?> {
            expect('{')
            val out = LinkedHashMap<String, Any?>()
            skipWs()
            if (peek() == '}') { pos++; return out }
            while (true) {
                skipWs()
                val key = readString()
                skipWs()
                expect(':')
                out[key] = readValue()
                skipWs()
                when (peek()) {
                    ',' -> pos++
                    '}' -> { pos++; return out }
                    else -> throw IllegalArgumentException("expected ',' or '}' at $pos")
                }
            }
        }

        private fun readArray(): List<Any?> {
            expect('[')
            val out = ArrayList<Any?>()
            skipWs()
            if (peek() == ']') { pos++; return out }
            while (true) {
                out.add(readValue())
                skipWs()
                when (peek()) {
                    ',' -> pos++
                    ']' -> { pos++; return out }
                    else -> throw IllegalArgumentException("expected ',' or ']' at $pos")
                }
            }
        }

        private fun readString(): String {
            expect('"')
            val sb = StringBuilder()
            while (true) {
                require(pos < s.length) { "unterminated string" }
                when (val c = s[pos++]) {
                    '"' -> return sb.toString()
                    '\\' -> {
                        require(pos < s.length) { "unterminated escape" }
                        when (val e = s[pos++]) {
                            '"' -> sb.append('"')
                            '\\' -> sb.append('\\')
                            '/' -> sb.append('/')
                            'b' -> sb.append('\b')
                            'f' -> sb.append('\u000C')
                            'n' -> sb.append('\n')
                            'r' -> sb.append('\r')
                            't' -> sb.append('\t')
                            'u' -> {
                                require(pos + 4 <= s.length) { "truncated \\u escape" }
                                sb.append(s.substring(pos, pos + 4).toInt(16).toChar())
                                pos += 4
                            }
                            else -> throw IllegalArgumentException("bad escape '\\$e' at $pos")
                        }
                    }
                    else -> sb.append(c)
                }
            }
        }

        private fun readNumber(): Double {
            val start = pos
            if (peek() == '-') pos++
            while (pos < s.length && (s[pos].isDigit() || s[pos] in ".eE+-")) pos++
            return s.substring(start, pos).toDoubleOrNull()
                ?: throw IllegalArgumentException("bad number at $start")
        }

        private fun <T> readLiteral(literal: String, value: T): T {
            require(s.startsWith(literal, pos)) { "bad literal at $pos" }
            pos += literal.length
            return value
        }

        private fun peek(): Char {
            require(pos < s.length) { "unexpected end of input" }
            return s[pos]
        }

        private fun expect(c: Char) {
            require(pos < s.length && s[pos] == c) { "expected '$c' at $pos" }
            pos++
        }
    }
}
