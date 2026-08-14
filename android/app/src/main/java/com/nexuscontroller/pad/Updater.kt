package com.nexuscontroller.pad

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import androidx.annotation.VisibleForTesting
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.security.MessageDigest

/**
 * The half of updating that opens sockets and talks to the package installer.
 *
 * [UpdateCheck] decides *what* to install; this decides nothing. The split is the
 * same one the server keeps, and for the same reason: everything worth testing
 * lives on the other side of it, and the suite runs without a phone.
 *
 * Two things about installing an APK from inside an app are worth knowing before
 * reading further. The user has to grant "install unknown apps" to *this* app
 * once, on Android 8 and newer — there is no way around it and no way to ask for
 * it silently; and the install itself is confirmed by a system dialog we do not
 * own, which is why the outcome arrives at a broadcast receiver rather than as
 * the return value of a function.
 */
sealed class UpdateStatus {
    object Idle : UpdateStatus()
    object Checking : UpdateStatus()
    /** No newer release, or no releases at all. Both are ordinary. */
    object UpToDate : UpdateStatus()
    data class Available(val release: ReleaseInfo) : UpdateStatus()
    data class Downloading(val percent: Int) : UpdateStatus()
    /** Handed to the system installer; the confirmation dialog is up. */
    object Installing : UpdateStatus()

    /**
     * The system says it went in. Its own outcome, not [Idle]: an outcome is
     * kept for a screen that comes back, and "idle" kept that way is a screen
     * that says nothing about what just happened and skips the version check
     * because it thinks something already answered.
     *
     * Unlike the PC, this is *not* a terminal state — [isActionable] still lets
     * the button be pressed. There, installing again would move the new build
     * aside as if it were the old one and destroy the only copy of what came
     * before; here the package installer simply puts the same APK over itself,
     * and the process is normally replaced before any of it can happen anyway.
     */
    object Installed : UpdateStatus()
    /** Reported to the user; never thrown away silently. */
    data class Failed(val message: String) : UpdateStatus()
}

/**
 * Whether tapping the update button now would do anything.
 *
 * False exactly while something is already running, so a second tap cannot start
 * a second download on top of the first.
 */
fun UpdateStatus.isActionable(): Boolean = when (this) {
    is UpdateStatus.Checking, is UpdateStatus.Downloading, is UpdateStatus.Installing -> false
    else -> true
}

class Updater(
    private val context: Context,
    /**
     * How the bytes are fetched. Replaced in tests, which is the only way any of
     * this is testable at all: the rules that matter — refusing a redirect off
     * https, refusing a body larger than the cap, reporting progress — sat
     * behind `URL.openConnection()` and could not be reached without a network
     * and a real GitHub release.
     */
    private val downloader: Downloader = Downloader(),
) {

    /**
     * Whether this build can install anything at all.
     *
     * From Android 8 the permission is per-app and granted by the user in
     * Settings, so it is asked about rather than requested. Below that the
     * manifest permission is the whole story.
     */
    fun canInstallPackages(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.packageManager.canRequestPackageInstalls()
        } else {
            true
        }

    /** Send the user to the one screen where the above can be turned on. */
    fun openInstallPermissionSettings() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        runCatching {
            context.startActivity(
                Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:${context.packageName}")
                ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
    }

    fun openReleasesPage() {
        runCatching {
            context.startActivity(
                Intent(Intent.ACTION_VIEW, Uri.parse(UpdateCheck.RELEASES_PAGE))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
        }
    }

    /**
     * Ask GitHub what the latest release is.
     *
     * Every failure — no network, a captive portal, a repository with no releases
     * — comes back as [UpdateStatus.UpToDate] or [UpdateStatus.Failed] rather
     * than an exception. This runs on a phone that is very often on a LAN with no
     * route to the internet, which is the normal way to use this app.
     */
    suspend fun check(currentVersion: String, flavor: String): UpdateStatus =
        withContext(Dispatchers.IO) {
            val body = try {
                downloader.read(UpdateCheck.RELEASE_API, MAX_JSON_BYTES)
            } catch (e: Exception) {
                Log.i(TAG, "update check failed: $e")
                return@withContext UpdateStatus.Failed(e.messageForUser())
            }
            val release = UpdateCheck.parseRelease(String(body, Charsets.UTF_8))
                ?: return@withContext UpdateStatus.UpToDate
            if (!UpdateCheck.isNewer(currentVersion, release.version)) {
                return@withContext UpdateStatus.UpToDate
            }
            // A release that does not carry an APK for *this* flavour is not an
            // update for this phone, however new its number is.
            if (release.url(UpdateCheck.assetFor(flavor)) == null) {
                return@withContext UpdateStatus.UpToDate
            }
            UpdateStatus.Available(release)
        }

    /**
     * Download the APK for this flavour, check it, and hand it to the installer.
     *
     * The return value only says the APK reached the system. What the user then
     * chooses in the confirmation dialog arrives at [InstallResultReceiver].
     */
    suspend fun downloadAndInstall(
        release: ReleaseInfo,
        flavor: String,
        onProgress: (Int) -> Unit
    ): UpdateStatus = withContext(Dispatchers.IO) {
        val name = UpdateCheck.assetFor(flavor)
        val url = release.url(name)
            ?: return@withContext UpdateStatus.Failed("This release has no $name")
        val sumsUrl = release.url(UpdateCheck.CHECKSUMS_ASSET)
            ?: return@withContext UpdateStatus.Failed("This release ships no checksums to verify against")

        // Straight to a file rather than into memory. The APK is around 55 MB;
        // held as a ByteArray it was that much on the heap, doubled by the copy
        // toByteArray() makes, and the cap allowed 200 MB — on the old phones the
        // legacy flavour exists for, that is an OutOfMemoryError rather than an
        // update. The file goes in the cache directory, where the system can
        // reclaim it if we somehow fail to.
        val staged = File.createTempFile("nexus-update", ".apk", context.cacheDir)
        try {
            val payload = try {
                val sums = String(downloader.read(sumsUrl, MAX_JSON_BYTES), Charsets.UTF_8)
                downloader.readTo(url, staged, MAX_APK_BYTES, onProgress)
                if (!UpdateCheck.matchesChecksum(sha256(staged), name, sums)) {
                    return@withContext UpdateStatus.Failed(
                        "The download does not match its checksum — try again"
                    )
                }
                staged
            } catch (e: Exception) {
                Log.w(TAG, "download failed", e)
                return@withContext UpdateStatus.Failed(e.messageForUser())
            }

            try {
                commit(payload)
            } catch (e: Exception) {
                Log.w(TAG, "could not hand the APK to the installer", e)
                return@withContext UpdateStatus.Failed(e.messageForUser())
            }
        } finally {
            // The installer has its own copy by now, committed or abandoned.
            if (!staged.delete()) Log.i(TAG, "could not delete $staged")
        }
        UpdateStatus.Installing
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(COPY_BUFFER)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    /** Write the APK into an install session and commit it.
     *
     * Anything that goes wrong between creating the session and committing it
     * has to abandon it. A session that is neither committed nor abandoned stays
     * staged, holding its copy of the APK, and Android allows an app only so
     * many at once — so a failure that repeats (a full disk, a truncated file)
     * ends with createSession refusing outright, and the update feature stops
     * working for reasons that have nothing to do with updating.
     */
    private fun commit(payload: File) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(
            PackageInstaller.SessionParams.MODE_FULL_INSTALL
        )
        params.setAppPackageName(context.packageName)
        val sessionId = installer.createSession(params)
        try {
            writeAndCommit(installer, sessionId, payload)
        } catch (e: Exception) {
            runCatching { installer.abandonSession(sessionId) }
                .onFailure { Log.w(TAG, "could not abandon session $sessionId", it) }
            throw e
        }
    }

    private fun writeAndCommit(
        installer: PackageInstaller,
        sessionId: Int,
        payload: File
    ) {
        installer.openSession(sessionId).use { session ->
            session.openWrite(SESSION_NAME, 0, payload.length()).use { out ->
                // Copied in blocks: the APK is tens of megabytes and the phones
                // this reaches back to have hundreds of them in total.
                payload.inputStream().use { input -> input.copyTo(out, COPY_BUFFER) }
                session.fsync(out)
            }
            val intent = Intent(context, InstallResultReceiver::class.java)
                .setAction(InstallResultReceiver.ACTION)
            // FLAG_MUTABLE from Android 12: the system fills the result in, so an
            // immutable one would come back empty — and on 12 and 13 creating one
            // without either flag throws outright.
            val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
            } else {
                PendingIntent.FLAG_UPDATE_CURRENT
            }
            val pending = PendingIntent.getBroadcast(context, sessionId, intent, flags)
            session.commit(pending.intentSender)
        }
    }

    private fun Exception.messageForUser(): String =
        message?.takeIf { it.isNotBlank() } ?: javaClass.simpleName

    companion object {
        private const val TAG = "NexusUpdater"
        private const val SESSION_NAME = "nexus"
        private const val COPY_BUFFER = 64 * 1024
        private const val MAX_JSON_BYTES = 1 shl 20
        //: Room for the APK to grow, without room for a "release" that is really
        //: a disk-filling exercise. Streamed to a file, so this is a ceiling on
        //: the cache directory rather than on the heap.
        private const val MAX_APK_BYTES = 200 shl 20
    }
}

/**
 * Where the system tells us what became of the install.
 *
 * The first thing it usually says is "ask the user" — the confirmation dialog is
 * an activity the installer wants *us* to start, which is the one part of this
 * flow that is not optional and not skippable.
 */
class InstallResultReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        when (val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, -1)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if (confirm == null) {
                    report(UpdateStatus.Failed("The installer asked for confirmation but sent no dialog"))
                    return
                }
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                runCatching { context.startActivity(confirm) }
                    .onFailure { report(UpdateStatus.Failed("Could not open the install dialog")) }
            }
            PackageInstaller.STATUS_SUCCESS -> report(UpdateStatus.Installed)
            else -> {
                val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
                Log.w("NexusUpdater", "install failed: status=$status message=$message")
                report(UpdateStatus.Failed(installFailure(status, message)))
            }
        }
    }

    private fun installFailure(status: Int, message: String?): String = when {
        // The one failure worth naming, because the fix is not obvious and the
        // system's own wording ("app not installed") explains nothing: this phone
        // has a copy signed by a different key. Every release before signing was
        // set up is such a copy.
        message?.contains("INSTALL_FAILED_UPDATE_INCOMPATIBLE") == true ||
            message?.contains("signatures do not match") == true ->
            "The installed copy was signed with a different key — uninstall this app first, " +
                "then install the update. Layouts saved on the phone will be lost."
        status == PackageInstaller.STATUS_FAILURE_ABORTED -> "Update cancelled"
        message.isNullOrBlank() -> "The system refused to install the update"
        else -> message
    }

    companion object {
        const val ACTION = "com.nexuscontroller.pad.INSTALL_RESULT"

        /**
         * The screen currently waiting for the outcome, set through [attach].
         *
         * Static state rather than anything cleverer because the receiver is
         * created by the system and gets no constructor arguments. What it is
         * not, any more, is the whole story: a screen can be away when the
         * answer comes, so the answer waits in [pending] rather than being
         * addressed to whoever happens to be here.
         */
        private var listener: ((UpdateStatus) -> Unit)? = null

        /**
         * The last outcome nobody was listening for, kept until somebody is.
         *
         * The install dialog belongs to the system, and while it is up this
         * activity is stopped — rotate the phone there, or let the system
         * recreate the activity for any of its own reasons, and the screen that
         * registered is gone for a moment. A result arriving in that moment used
         * to be dropped, leaving the About screen saying "Installing…" for ever
         * with no way back except leaving the screen. Delivered once: whoever
         * takes it clears it.
         */
        private var pending: UpdateStatus? = null
        private var pendingAt = 0L

        /**
         * How long an undelivered outcome is still worth showing.
         *
         * It is the answer to something the user did seconds ago, not a message.
         * Kept for ever it becomes one: leave the About screen while the dialog
         * is up, come back in the evening, and the first thing the screen says is
         * "Update cancelled" — about an afternoon nobody remembers — while the
         * version check that would have said something true is held back for it.
         */
        private const val PENDING_TTL_MS = 60_000L

        /**
         * Milliseconds since boot, *including* the time the phone spent asleep.
         *
         * Not nanoTime(): it stops in deep sleep, which is exactly the scenario
         * the cap above exists for — the phone in a pocket all evening comes
         * back with a clock that has barely moved and an outcome that is
         * therefore still "fresh". Tests replace this.
         */
        @VisibleForTesting
        internal var now: () -> Long = { SystemClock.elapsedRealtime() }

        /** Listen for the outcome, receiving one that arrived just before. */
        fun attach(listener: (UpdateStatus) -> Unit) {
            // Decided under the lock, delivered outside it. Nothing this
            // listener does takes the lock today, but it is a callback into
            // Compose and the rule everywhere else here is that no lock is held
            // while calling out of the class that owns it.
            val replay = claim(listener)
            if (replay != null) listener(replay)
        }

        @Synchronized
        private fun claim(listener: (UpdateStatus) -> Unit): UpdateStatus? {
            this.listener = listener
            val waiting = pending
            pending = null
            return if (waiting != null && now() - pendingAt < PENDING_TTL_MS) waiting else null
        }

        @Synchronized
        fun detach(listener: (UpdateStatus) -> Unit) {
            // Only if it is still ours: a screen being disposed after its
            // replacement has already registered must not silence the new one.
            if (this.listener === listener) this.listener = null
        }

        /** Hand an outcome to the screen waiting for it, or keep it until one is. */
        internal fun report(status: UpdateStatus) = waiting(status)?.invoke(status)

        /** Take the listener to deliver to, or park the outcome for the next one. */
        @Synchronized
        private fun waiting(status: UpdateStatus): ((UpdateStatus) -> Unit)? {
            val current = listener
            if (current == null) {
                pending = status
                pendingAt = now()
            }
            return current
        }
    }
}
