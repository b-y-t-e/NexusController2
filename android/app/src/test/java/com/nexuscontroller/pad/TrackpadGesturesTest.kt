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

    @Test
    fun `a gesture that never ended is let go of by the next one`() {
        """Compose can cancel a pointer loop between the first down and the last
        up, and then end() never runs. The button would stay pressed on the PC,
        and — because a drag is deliberately never a scroll — every later
        two-finger move would be sent as movement instead of scrolling."""
        val g = machine()
        g.begin(1_000)
        g.end(1_050)
        g.begin(1_100)                       // latches: dragging
        assertTrue(g.isDragging)

        // No end(). The next gesture has to clean up after it.
        assertEquals(listOf(TrackpadAction.Release(MouseButton.LEFT)), g.begin(2_000))
        assertFalse(g.isDragging)

        val actions = g.update(pointers = 2, dx = 10f, dy = 0f, spread = 90f)
        assertEquals(listOf(TrackpadAction.Scroll(8f, 0f)), actions)
    }
}

class HeldButtonsTest {

    @Test
    fun `nothing is held to begin with`() {
        val held = HeldButtons()
        assertEquals(0, held.mask)
        assertFalse(held.isHeld(MouseButton.LEFT))
    }

    @Test
    fun `a tap ending does not let go of a drag that started meanwhile`() {
        """The race that broke a fast "tap and a half".

        The tap holds its button for 35 ms from a coroutine. Touch again inside
        that window and the drag presses the same button — then the tap woke up
        and put back what it found before, which was "not held", and the
        selection died a few milliseconds after it began.
        """
        val held = HeldButtons()
        held.byTap(MouseButton.LEFT, true)
        held.byGesture(MouseButton.LEFT, true)      // the latch, 20 ms later

        held.byTap(MouseButton.LEFT, false)         // the tap's 35 ms are up

        assertTrue("the drag must survive the tap ending", held.isHeld(MouseButton.LEFT))
    }

    @Test
    fun `a tap on the glass does not let go of a button held on the bar`() {
        val held = HeldButtons()
        held.byBar(MouseButton.LEFT, true)
        held.byTap(MouseButton.LEFT, true)
        held.byTap(MouseButton.LEFT, false)
        assertTrue(held.isHeld(MouseButton.LEFT))
    }

    @Test
    fun `the button goes up when the last source lets go`() {
        val held = HeldButtons()
        held.byBar(MouseButton.RIGHT, true)
        held.byTap(MouseButton.RIGHT, true)
        held.byTap(MouseButton.RIGHT, false)
        held.byBar(MouseButton.RIGHT, false)
        assertEquals(0, held.mask)
    }

    @Test
    fun `the two buttons are independent`() {
        val held = HeldButtons()
        held.byBar(MouseButton.LEFT, true)
        held.byGesture(MouseButton.RIGHT, true)
        held.byGesture(MouseButton.RIGHT, false)
        assertTrue(held.isHeld(MouseButton.LEFT))
        assertFalse(held.isHeld(MouseButton.RIGHT))
    }

    @Test
    fun `the bits are the ones the wire carries`() {
        """PROTOCOL.md §MOUSE: bit0 = left, bit1 = right. tests/test_client_compat.py
        asserts the same two numbers on the server, because swapping them here
        would swap the buttons on the PC without failing anywhere."""
        assertEquals(1, MouseButton.LEFT.bit)
        assertEquals(2, MouseButton.RIGHT.bit)
    }

    @Test
    fun `the mask says which button is down, and the bar reads it the same way`() {
        """The button bar draws its two keys from the mask, and did it with 1 and
        2 written out again where nothing would have caught them drifting."""
        val held = HeldButtons()
        held.byBar(MouseButton.RIGHT, true)
        assertEquals(0, held.mask and MouseButton.LEFT.bit)
        assertTrue(held.mask and MouseButton.RIGHT.bit != 0)
        held.byGesture(MouseButton.LEFT, true)
        assertTrue(held.mask and MouseButton.LEFT.bit != 0)
    }

    @Test
    fun `releaseAll drops every source at once`() {
        """For a surface going away: whatever is holding a button, nobody is
        going to lift it afterwards."""
        val held = HeldButtons()
        held.byBar(MouseButton.LEFT, true)
        held.byGesture(MouseButton.RIGHT, true)
        held.byTap(MouseButton.LEFT, true)
        held.releaseAll()
        assertEquals(0, held.mask)
    }
}
