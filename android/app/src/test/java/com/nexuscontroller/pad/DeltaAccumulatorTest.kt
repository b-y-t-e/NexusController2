package com.nexuscontroller.pad

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Continuous movement into the whole-number steps `MOUSE` and `SCROLL` carry.
 *
 * Both failures this guards against were felt on the glass rather than seen in
 * a log: a slow drag that moved nothing at all, and a fast one that moved a
 * fraction of the distance and then jumped at the start of the next gesture.
 */
class DeltaAccumulatorTest {

    @Test
    fun `sub-pixel movement is kept until it is worth a whole step`() {
        val acc = DeltaAccumulator()
        assertTrue(acc.add(0.4f, 0f).isEmpty())
        assertTrue(acc.add(0.4f, 0f).isEmpty())
        assertEquals(listOf(1 to 0), acc.add(0.4f, 0f))
    }

    @Test
    fun `a flick leaves in one call, as however many messages it takes`() {
        // 300 px between two pointer events is one wire byte's worth three times
        // over. Sending 127 and forgetting the rest is what made a fast swipe
        // move the cursor a third of the way.
        val steps = DeltaAccumulator().add(300f, 0f)
        assertEquals(listOf(127 to 0, 127 to 0, 46 to 0), steps)
        assertEquals(300, steps.sumOf { it.first })
    }

    @Test
    fun `nothing is carried into the next gesture`() {
        val acc = DeltaAccumulator()
        acc.add(300f, 0f)
        // The finger stopped: no further event ever comes for that gesture, so a
        // remainder kept here would ride out at the start of the next one.
        assertTrue(acc.add(0f, 0f).isEmpty())
    }

    @Test
    fun `both axes travel together`() {
        assertEquals(listOf(-127 to 100, -73 to 0), DeltaAccumulator().add(-200f, 100f))
    }

    @Test
    fun `an absurd burst is capped rather than becoming a runaway cursor`() {
        val acc = DeltaAccumulator(maxChunks = 2)
        assertEquals(listOf(127 to 0, 127 to 0), acc.add(5000f, 0f))
        assertTrue("the rest must be dropped, not queued", acc.add(0f, 0f).isEmpty())
    }

    @Test
    fun `the fraction survives a capped burst`() {
        val acc = DeltaAccumulator(maxChunks = 1)
        acc.add(200.5f, 0f)
        assertTrue(acc.add(0.4f, 0f).isEmpty())
        assertEquals(listOf(1 to 0), acc.add(0.2f, 0f))
    }

    @Test
    fun `reset forgets a partial step`() {
        val acc = DeltaAccumulator()
        acc.add(0.9f, 0.9f)
        acc.reset()
        assertTrue(acc.add(0.2f, 0.2f).isEmpty())
    }
}
