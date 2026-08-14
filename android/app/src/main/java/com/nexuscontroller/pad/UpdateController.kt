package com.nexuscontroller.pad

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch

/**
 * Updating, for the whole app rather than for one screen.
 *
 * It used to live inside `AboutScreen`, which meant two things: the only way to
 * hear about a new version was to go looking for it, and a download died the
 * moment that screen was closed, because the coroutine belonged to its
 * composition. One controller, held by the activity, fixes both — the check runs
 * once when the app starts, the main menu can offer the update, and About shows
 * the same state rather than a second copy of it.
 *
 * Who may write [status] is still [UpdateScreenState]'s rule, and it is still
 * tested there: an outcome from the system installer wins and keeps winning
 * until the user asks for something new by hand.
 */
class UpdateController(
    private val updater: Updater,
    private val scope: CoroutineScope,
    private val version: String = BuildConfig.VERSION_NAME,
    private val flavor: String = BuildConfig.FLAVOR,
) {
    var status by mutableStateOf<UpdateStatus>(UpdateStatus.Idle)
        private set

    private val screen = UpdateScreenState()

    /** Handed to [InstallResultReceiver]; see [rememberUpdateController]. */
    val onInstallResult: (UpdateStatus) -> Unit = { result ->
        screen.fromInstaller()
        status = result
    }

    /** Is there something to offer, and what is it called. */
    val available: ReleaseInfo? get() = (status as? UpdateStatus.Available)?.release

    /** True while something is happening that the user should see rather than start again. */
    val busy: Boolean
        get() = status is UpdateStatus.Checking ||
            status is UpdateStatus.Downloading ||
            status is UpdateStatus.Installing

    /**
     * The check that runs by itself when the app starts.
     *
     * Says nothing when there is nothing to say: an app for playing games in the
     * same room as the PC has no business interrupting anybody about a point
     * release, so a quiet check leaves [status] as it found it unless a release
     * is actually newer. Only what the user asked for gets to say "you are up to
     * date" or "GitHub could not be reached".
     */
    fun checkOnStart() {
        if (status != UpdateStatus.Idle) return
        scope.launch {
            val result = updater.check(version, flavor)
            if (screen.fromWork() && result is UpdateStatus.Available) status = result
        }
    }

    /** The check somebody pressed a button for. Every outcome is shown. */
    fun checkNow() {
        if (busy) return
        screen.userAsked()
        status = UpdateStatus.Checking
        scope.launch {
            val result = updater.check(version, flavor)
            if (screen.fromWork()) status = result
        }
    }

    /** Download and hand the APK to the system installer. */
    fun install(release: ReleaseInfo) {
        if (busy) return
        screen.userAsked()
        status = UpdateStatus.Downloading(0)
        scope.launch {
            val result = updater.downloadAndInstall(release, flavor) { percent ->
                if (screen.fromWork()) status = UpdateStatus.Downloading(percent)
            }
            // The broadcast can beat this line: downloadAndInstall returns
            // "Installing" as soon as the session is committed, and the user's
            // answer to the system dialog — most often "no" — arrives through the
            // receiver meanwhile. Assigning over it put "Installing…" back on
            // screen with nothing left to change it.
            if (screen.fromWork()) status = result
        }
    }

    /**
     * One tap, whatever the state: check, ask for the permission, or install.
     *
     * The button in About and the entry in the main menu are the same decision,
     * so it is written once. The permission is asked for in Settings and not by
     * us — from Android 8 it cannot be requested with a dialog — so sending the
     * user there is the whole of what an app may do about it.
     */
    fun act() {
        val release = available
        when {
            release == null -> checkNow()
            !updater.canInstallPackages() -> updater.openInstallPermissionSettings()
            else -> install(release)
        }
    }

    fun canInstallPackages(): Boolean = updater.canInstallPackages()

    /** The releases page in a browser — the way out when the in-app path cannot work. */
    fun openReleasesPage() = updater.openReleasesPage()
}

/**
 * The controller for this activity, listening for install outcomes while it lives.
 *
 * `attach`, not a plain assignment: a result landing while no screen is
 * registered waits for the next one instead of being dropped — see
 * [InstallResultReceiver].
 */
@Composable
fun rememberUpdateController(): UpdateController {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val controller = remember { UpdateController(Updater(context), scope) }
    DisposableEffect(controller) {
        InstallResultReceiver.attach(controller.onInstallResult)
        onDispose { InstallResultReceiver.detach(controller.onInstallResult) }
    }
    return controller
}
