package com.nexuscontroller.pad

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TrackpadGesturesTest {

    private fun machine() = TrackpadGestures()

    /** A movement comfortably past the slop. */
    private val far = 8f

    // ------------------------------------------------------------------ taps

    @Test
    fun `a one finger tap is a left click`() {
        val g = machine()
        assertEquals(emptyList<TrackpadAction>(), g.begin(1_000))
        assertEquals(listOf(TrackpadAction.Tap(MouseButton.LEFT)), g.end(1_050))
    }

    @Test
    fun `a two finger tap is a right click`() {
        val g = machine()
        g.begin(1_000)
        g.update(pointers = 2, dx = 0f, dy = 0f, spread = 90f)
        assertEquals(listOf(TrackpadAction.Tap(MouseButton.RIGHT)), g.end(1_050))
    }

    @Test
    fun `two fingers resting together are still one finger`() {
        """A thumb next to an index finger must not turn a movement into a scroll."""
        val g = machine()
        g.begin(1_000)
        val actions = g.update(pointers = 2, dx = far, dy = 0f, spread = 4f)
        assertEquals(listOf(TrackpadAction.Move(far, 0f)), actions)
    }

    // ------------------------------------------------------------- moving

    @Test
    fun `one finger moves the cursor and clicks nothing`() {
        val g = machine()
        g.begin(1_000)
        assertEquals(listOf(TrackpadAction.Move(far, -far)), g.update(1, far, -far, 0f))
        assertEquals(emptyList<TrackpadAction>(), g.end(1_200))
    }

    @Test
    fun `two fingers scroll, geared down`() {
        val g = machine()
        g.begin(1_000)
        val actions = g.update(pointers = 2, dx = 10f, dy = 0f, spread = 90f)
        assertEquals(listOf(TrackpadAction.Scroll(8f, 0f)), actions)
    }

    @Test
    fun `a movement below the slop is ignored`() {
        """Otherwise a tap by a finger that is not perfectly still stops being a
        tap, and a click that needs a steady hand is a click nobody lands."""
        val g = machine()
        g.begin(1_000)
        assertEquals(emptyList<TrackpadAction>(), g.update(1, 0.5f, 0.5f, 0f))
        assertEquals(listOf(TrackpadAction.Tap(MouseButton.LEFT)), g.end(1_040))
    }

    // ------------------------------------------------- selection: tap and a half

    @Test
    fun `tap then touch again and move holds the button down`() {
        """The whole point: this is dragging a window and selecting text."""
        val g = machine()
        g.begin(1_000)
        g.end(1_050)

        assertEquals(listOf(TrackpadAction.Press(MouseButton.LEFT)), g.begin(1_150))
        assertTrue(g.isDragging)
        assertEquals(listOf(TrackpadAction.Move(far, far)), g.update(1, far, far, 0f))
        assertEquals(listOf(TrackpadAction.Release(MouseButton.LEFT)), g.end(1_600))
        assertFalse(g.isDragging)
    }

    @Test
    fun `a second touch that never moves is simply a double click`() {
        """Same gesture, and the right outcome either way: press, no movement,
        release is what the second click of a double click looks like."""
        val g = machine()
        g.begin(1_000)
        g.end(1_050)
        assertEquals(listOf(TrackpadAction.Press(MouseButton.LEFT)), g.begin(1_100))
        assertEquals(listOf(TrackpadAction.Release(MouseButton.LEFT)), g.end(1_140))
    }

    @Test
    fun `coming back too late is an ordinary tap again`() {
        val g = machine()
        g.begin(1_000)
        g.end(1_050)
        assertEquals(emptyList<TrackpadAction>(), g.begin(1_500))
        assertFalse(g.isDragging)
        assertEquals(listOf(TrackpadAction.Tap(MouseButton.LEFT)), g.end(1_550))
    }

    @Test
    fun `the latch is spent once and does not carry into a third touch`() {
        val g = machine()
        g.begin(1_000)
        g.end(1_050)
        g.begin(1_100)          // latches
        g.end(1_150)            // releases
        assertEquals(emptyList<TrackpadAction>(), g.begin(1_200))
    }

    @Test
    fun `a right click does not arm the latch`() {
        """Two fingers mean the other button; touching again is not a selection."""
        val g = machine()
        g.begin(1_000)
        g.update(pointers = 2, dx = 0f, dy = 0f, spread = 90f)
        g.end(1_050)
        assertEquals(emptyList<TrackpadAction>(), g.begin(1_100))
    }

    @Test
    fun `a plain drag does not arm the latch either`() {
        val g = machine()
        g.begin(1_000)
        g.update(1, far, far, 0f)
        g.end(1_100)
        assertEquals(emptyList<TrackpadAction>(), g.begin(1_150))
    }

    @Test
    fun `a second finger during a selection does not turn it into a scroll`() {
        val g = machine()
        g.begin(1_000)
        g.end(1_050)
        g.begin(1_100)
        val actions = g.update(pointers = 2, dx = 10f, dy = 0f, spread = 90f)
        assertEquals(listOf(TrackpadAction.Move(10f, 0f)), actions)
    }

    // ----------------------------------------------------------------- cancel

    @Test
    fun `cancelling mid selection lets the button go`() {
        """The surface can disappear under a finger — the mode changes, the
        connection drops. A button held by a gesture that no longer exists is a
        button nobody will ever lift."""
        val g = machine()
        g.begin(1_000)
        g.end(1_050)
        g.begin(1_100)
        assertEquals(listOf(TrackpadAction.Release(MouseButton.LEFT)), g.cancel())
        assertFalse(g.isDragging)
    }

    @Test
    fun `cancelling with nothing held says nothing`() {
        assertEquals(emptyList<TrackpadAction>(), machine().cancel())
    }
}
