package com.nexuscontroller.pad

/**
 * Serialisation of on-screen layouts.
 *
 * Kept free of Android and Compose types so it is unit-testable; [CompConfig] holds the
 * same fields but as observable Compose state, and converts through [LayoutEntry].
 *
 * The stored form is the historical JSON shape, so layouts written by v1 of the app keep
 * loading:  `{"FACE":{"x":1.0,"y":2.0,"s":1.0,"r":0.0,"k":0,"turbo":false}}`
 */
data class LayoutEntry(
    val x: Float,
    val y: Float,
    val scale: Float = 1f,
    val rotation: Float = 0f,
    val mappedKey: Int = 0,
    val isTurbo: Boolean = false
)

object LayoutSerializer {

    /**
     * Preference key for a profile. Xbox and DualShock share one gamepad layout (the
     * components are identical, only the glyphs differ), Buzz gets its own so switching
     * controller type never scrambles a saved gamepad layout.
     */
    fun prefsKey(profile: String, type: ControllerType): String =
        if (type.isGamepad) "layout_json_$profile" else "layout_json_${profile}_buzz"

    fun encode(configs: Map<String, LayoutEntry>): String {
        val sb = StringBuilder("{")
        var first = true
        for ((key, e) in configs) {
            if (!first) sb.append(',')
            first = false
            sb.append(quote(key)).append(":{")
                .append("\"x\":").append(num(e.x)).append(',')
                .append("\"y\":").append(num(e.y)).append(',')
                .append("\"s\":").append(num(e.scale)).append(',')
                .append("\"r\":").append(num(e.rotation)).append(',')
                .append("\"k\":").append(e.mappedKey).append(',')
                .append("\"turbo\":").append(e.isTurbo)
                .append('}')
        }
        return sb.append('}').toString()
    }

    /** Returns an empty map when the payload is missing or unparseable. */
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

    private fun Map<String, Any?>.numberOr(key: String, fallback: Float?): Float? =
        (this[key] as? Double)?.toFloat() ?: fallback

    private fun num(v: Float): String = if (v.isFinite()) v.toDouble().toString() else "0.0"

    private fun quote(s: String): String {
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
}

/**
 * Minimal JSON reader for the flat layout documents above. Deliberately tiny: it exists so
 * layout persistence has no `org.json` (i.e. no Android) dependency and can be tested on
 * the JVM. Objects become `Map<String, Any?>`, numbers become `Double`.
 */
internal object MiniJson {

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
