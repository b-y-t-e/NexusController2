package com.nexuscontroller.pad

/**
 * Turns continuous finger movement into the whole-number steps the wire carries.
 *
 * Two problems, one place:
 *
 * * **fractions.** A slow drag moves a fraction of a pixel per pointer event,
 *   and `toInt()` on each one separately is zero every time — the cursor would
 *   simply not move. What is left over stays here and goes out later.
 * * **flicks.** `MOUSE` carries one signed byte per axis (PROTOCOL.md §MOUSE),
 *   so a 300-pixel flick between two pointer events does not fit in one
 *   message. Sending a clamped 127 and taking the whole 300 out of the
 *   accumulator threw the rest away, and the swipe moved the cursor a fraction
 *   of the distance it travelled. Keeping the remainder for the *next* pointer
 *   event is no better: if the finger stops, that event never comes, and the
 *   leftover jumps the cursor at the start of the next gesture.
 *
 * So the whole distance leaves in one call, as however many messages it takes,
 * and only the fraction is carried over. [maxChunks] is the ceiling on that: a
 * thousand pixels between two events is not a hand, and a burst that big should
 * not become a cursor that runs on by itself.
 */
class DeltaAccumulator(private val maxChunks: Int = 8) {
    private var accX = 0f
    private var accY = 0f

    /** The steps to send now. Empty when there is not yet a whole pixel to send. */
    fun add(dx: Float, dy: Float): List<Pair<Int, Int>> {
        accX += dx
        accY += dy
        val steps = mutableListOf<Pair<Int, Int>>()
        repeat(maxChunks) {
            val ix = Protocol.clampAxis(accX.toInt())
            val iy = Protocol.clampAxis(accY.toInt())
            if (ix == 0 && iy == 0) return steps
            steps += ix to iy
            accX -= ix
            accY -= iy
        }
        // Over the ceiling: drop what is left but keep the fraction, so the
        // sub-pixel behaviour above survives a burst.
        accX -= accX.toInt()
        accY -= accY.toInt()
        return steps
    }

    /** Forget any partial movement — for a gesture that was cancelled. */
    fun reset() {
        accX = 0f
        accY = 0f
    }
}
