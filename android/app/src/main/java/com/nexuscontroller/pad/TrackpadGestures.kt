package com.nexuscontroller.pad

/** The two buttons a gesture can resolve to. Middle is not reachable by touch. */
enum class MouseButton { LEFT, RIGHT }

/** What the trackpad decided a touch means. */
sealed interface TrackpadAction {
    data class Move(val dx: Float, val dy: Float) : TrackpadAction
    data class Scroll(val dx: Float, val dy: Float) : TrackpadAction

    /** A click. Pressing and releasing are one decision; the caller times the gap. */
    data class Tap(val button: MouseButton) : TrackpadAction

    /** Held down until the matching [Release] — this is what a selection is made of. */
    data class Press(val button: MouseButton) : TrackpadAction
    data class Release(val button: MouseButton) : TrackpadAction
}

/**
 * The trackpad's gesture rules, with no Android in them.
 *
 * The rules used to live inside the pointer loop of a composable, where this
 * project has no way to test them at all — there is no Compose UI test harness
 * here and adding one for four gestures would cost more than it returns. As a
 * plain state machine fed by (fingers, movement, time) they are ordinary JUnit
 * material, and the composable is left with nothing but plumbing.
 *
 * The vocabulary is the one every precision touchpad uses, so that muscle memory
 * from a laptop transfers:
 *
 * * one finger moves the cursor, two scroll;
 * * a tap is a left click, a two-finger tap a right click;
 * * **tap, then touch again within [latchWindowMs] and move — "tap and a half" —
 *   holds the left button down for as long as the finger stays on the glass.**
 *   That is dragging a window and selecting text, the one thing this trackpad
 *   could not do at all.
 *
 * A second tap that never moves is the same gesture as the start of a drag, and
 * ends up as a second click — which is exactly what a double tap should be.
 */
class TrackpadGestures(
    /** Squared pixels; below this a touch is still a tap, not a movement. */
    private val moveSlopSquared: Float = 1.5f,
    /** How far apart two fingers must be before they count as two (pixels). */
    private val twoFingerSpread: Float = 25f,
    /** How long after a tap a new touch still latches into a drag. */
    private val latchWindowMs: Long = 300L,
    /** Scrolling is geared down slightly; a finger travels further than a wheel. */
    private val scrollScale: Float = 0.8f,
) {
    private var maxPointers = 0
    private var moved = false
    private var dragging = false

    /**
     * When the last plain tap lifted, or null when there is nothing to latch on.
     *
     * Null rather than a sentinel time: with `Long.MIN_VALUE` the very first
     * touch of all latched, because `now - Long.MIN_VALUE` overflows to a
     * negative number and reads as "no time at all has passed". The trackpad
     * would have grabbed whatever was under the first finger after every launch.
     */
    private var lastTapEndedAt: Long? = null

    /** True while a selection is in progress, for the caller's own feedback. */
    val isDragging: Boolean get() = dragging

    /** First finger down. */
    fun begin(now: Long): List<TrackpadAction> {
        maxPointers = 1
        moved = false
        val latched = lastTapEndedAt?.let { now - it <= latchWindowMs } == true
        // Consumed either way: a third touch in quick succession is a new
        // gesture, not a second chance to start dragging.
        lastTapEndedAt = null
        if (!latched) return emptyList()
        dragging = true
        return listOf(TrackpadAction.Press(MouseButton.LEFT))
    }

    /**
     * One pointer event: how many fingers are down, how far they moved on
     * average since the last event, and how far apart the first two are.
     */
    fun update(pointers: Int, dx: Float, dy: Float, spread: Float): List<TrackpadAction> {
        // Two fingers resting together are one finger as far as the user is
        // concerned — a thumb alongside an index finger should not turn a
        // cursor movement into a scroll.
        if (pointers > maxPointers && (pointers < 2 || spread > twoFingerSpread)) {
            maxPointers = pointers
        }
        if (dx == 0f && dy == 0f) return emptyList()
        if (!moved && (dx * dx + dy * dy) > moveSlopSquared) moved = true
        if (!moved) return emptyList()

        // A drag is a drag: once the button is down, a second finger landing
        // must not turn the rest of the selection into a scroll.
        if (dragging || maxPointers < 2) return listOf(TrackpadAction.Move(dx, dy))
        return listOf(TrackpadAction.Scroll(dx * scrollScale, dy * scrollScale))
    }

    /** Last finger up. */
    fun end(now: Long): List<TrackpadAction> {
        if (dragging) {
            dragging = false
            return listOf(TrackpadAction.Release(MouseButton.LEFT))
        }
        if (moved) return emptyList()
        if (maxPointers >= 2) return listOf(TrackpadAction.Tap(MouseButton.RIGHT))
        // Only a plain one-finger tap arms the latch; coming back within the
        // window is then the "and a half" that starts a drag.
        lastTapEndedAt = now
        return listOf(TrackpadAction.Tap(MouseButton.LEFT))
    }

    /**
     * Forget everything, releasing whatever is held.
     *
     * Called when the surface goes away — switching modes, or the connection
     * dropping — because a button held by a gesture that no longer exists is a
     * button nobody will ever lift.
     */
    fun cancel(): List<TrackpadAction> {
        maxPointers = 0
        moved = false
        lastTapEndedAt = null
        if (!dragging) return emptyList()
        dragging = false
        return listOf(TrackpadAction.Release(MouseButton.LEFT))
    }
}
