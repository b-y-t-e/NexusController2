package com.nexuscontroller.pad

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Handing the install's outcome back to the screen that asked for it.
 *
 * The receiver itself needs a real system to fire; what is testable — and what
 * was wrong — is the hand-off around it. The confirmation dialog belongs to the
 * system installer, so while it is up this app's activity is stopped and can be
 * recreated under it. The About screen registered on composition and cleared the
 * field on disposal, so a result arriving in that gap went nowhere and the
 * screen kept saying "Installing…" with nothing left to change it.
 */
class InstallResultTest {

    /** Moved by hand in every test: static state and a real clock in the same
     *  suite is a test that passes until the machine is busy. */
    private var clock = 0L

    @Before
    fun useAFakeClock() {
        clock = 0L
        InstallResultReceiver.now = { clock }
    }

    // ------------------------------------------------ who may write the status

    @Test
    fun `an outcome from the installer is not written over by a check`() {
        """The screen is recreated while the system dialog is up, so the outcome
        arrives at the new composition — which was about to run a routine version
        check and say "you are up to date", which is not even true until the app
        restarts."""
        val screen = UpdateScreenState()
        screen.fromInstaller()
        assertFalse(screen.fromWork())
    }

    @Test
    fun `nor by the call that started the install`() {
        """downloadAndInstall returns "Installing" once the session is committed,
        and the user's "no" comes back through the receiver meanwhile."""
        val screen = UpdateScreenState()
        screen.fromInstaller()
        assertFalse(screen.fromWork())   // the progress callback
        assertFalse(screen.fromWork())   // and the return value
    }

    @Test
    fun `and stays until the user asks for something new`() {
        val screen = UpdateScreenState()
        screen.fromInstaller()
        screen.userAsked()
        assertTrue(screen.fromWork())
    }

    @Test
    fun `with nothing standing, work writes freely`() {
        val screen = UpdateScreenState()
        assertTrue(screen.fromWork())
        assertTrue(screen.fromWork())
    }

    @Test
    fun `an outcome nobody came back for is not shown an hour later`() {
        """It answers something the user did seconds ago. Kept indefinitely it
        becomes a message about an afternoon nobody remembers — and it would hold
        back the version check that has something true to say."""
        InstallResultReceiver.report(UpdateStatus.Failed("Update cancelled"))
        clock = 61_000L        // a minute and one second, in elapsedRealtime()'s units
        val seen = mutableListOf<UpdateStatus>()
        InstallResultReceiver.attach { seen.add(it) }
        assertTrue(seen.isEmpty())
    }

    @Test
    fun `one that arrived a moment ago still is`() {
        InstallResultReceiver.report(UpdateStatus.Failed("Update cancelled"))
        clock = 5_000L
        val seen = mutableListOf<UpdateStatus>()
        InstallResultReceiver.attach { seen.add(it) }
        assertEquals(1, seen.size)
    }

    @Test
    fun `a listener hears what arrives while it is attached`() {
        val seen = mutableListOf<UpdateStatus>()
        val listener: (UpdateStatus) -> Unit = { seen.add(it) }
        InstallResultReceiver.attach(listener)
        InstallResultReceiver.report(UpdateStatus.Failed("no"))
        assertEquals(listOf<UpdateStatus>(UpdateStatus.Failed("no")), seen)
        InstallResultReceiver.detach(listener)
    }

    @Test
    fun `an outcome that arrives with nobody listening is kept for whoever comes next`() {
        InstallResultReceiver.report(UpdateStatus.Failed("Update cancelled"))
        val seen = mutableListOf<UpdateStatus>()
        InstallResultReceiver.attach { seen.add(it) }
        assertEquals(listOf<UpdateStatus>(UpdateStatus.Failed("Update cancelled")), seen)
    }

    @Test
    fun `a successful install is its own outcome, not Idle`() {
        """Idle says nothing on screen and is what a fresh screen starts as, so
        replaying it to a screen recreated after the install left the user with a
        blank "check for updates" — and settled, so the check did not run."""
        InstallResultReceiver.report(UpdateStatus.Installed)
        val seen = mutableListOf<UpdateStatus>()
        InstallResultReceiver.attach { seen.add(it) }
        assertEquals(listOf<UpdateStatus>(UpdateStatus.Installed), seen)
    }

    @Test
    fun `it is delivered once, not to every screen that ever attaches`() {
        InstallResultReceiver.report(UpdateStatus.Idle)
        InstallResultReceiver.attach {}
        val later = mutableListOf<UpdateStatus>()
        InstallResultReceiver.attach { later.add(it) }
        assertTrue(later.isEmpty())
    }

    @Test
    fun `a screen going away after its replacement arrived does not silence it`() {
        """Compose disposes the old composition after the new one is in place, so
        detach() runs last and a plain "set it to null" took the new screen's
        listener away with it."""
        val old: (UpdateStatus) -> Unit = {}
        val new = mutableListOf<UpdateStatus>()
        val newer: (UpdateStatus) -> Unit = { new.add(it) }
        InstallResultReceiver.attach(old)
        InstallResultReceiver.attach(newer)
        InstallResultReceiver.detach(old)
        InstallResultReceiver.report(UpdateStatus.Failed("boom"))
        assertEquals(1, new.size)
    }

    @After
    fun tearDown() {
        // Left at a fixed point rather than put back to SystemClock, which is not
        // callable off a device — the next test's @Before sets its own anyway.
        InstallResultReceiver.now = { 0L }
        // Static state, so a leftover listener — or a leftover outcome — would be
        // another test's ghost. Attaching takes whatever is pending with it.
        val noop: (UpdateStatus) -> Unit = {}
        InstallResultReceiver.attach(noop)
        InstallResultReceiver.detach(noop)
    }
}
