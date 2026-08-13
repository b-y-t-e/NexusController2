package com.nexuscontroller.pad

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL

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

class Updater(private val context: Context) {

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
                fetch(UpdateCheck.RELEASE_API, MAX_JSON_BYTES)
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

        val payload = try {
            val sums = String(fetch(sumsUrl, MAX_JSON_BYTES), Charsets.UTF_8)
            val apk = fetch(url, MAX_APK_BYTES, onProgress)
            if (!UpdateCheck.matchesChecksum(apk, name, sums)) {
                return@withContext UpdateStatus.Failed(
                    "The download does not match its checksum — try again"
                )
            }
            apk
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
        UpdateStatus.Installing
    }

    /** Write the APK into an install session and commit it. */
    private fun commit(payload: ByteArray) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(
            PackageInstaller.SessionParams.MODE_FULL_INSTALL
        )
        val sessionId = installer.createSession(params)
        installer.openSession(sessionId).use { session ->
            session.openWrite(SESSION_NAME, 0, payload.size.toLong()).use { out ->
                out.write(payload)
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

    /** Read a URL into memory, capped, reporting progress when the size is known. */
    private fun fetch(
        url: String,
        limit: Int,
        onProgress: ((Int) -> Unit)? = null
    ): ByteArray {
        // Ours or nothing. The API URL is a constant and every asset URL was
        // checked against DOWNLOAD_PREFIX when the release was parsed, so this is
        // belt and braces — but it is the last point before bytes become an app.
        require(url.startsWith(UpdateCheck.DOWNLOAD_PREFIX) || url == UpdateCheck.RELEASE_API) {
            "refusing to download from $url"
        }
        var connection = URL(url).openConnection() as HttpURLConnection
        var redirects = 0
        try {
            while (true) {
                connection.connectTimeout = CONNECT_TIMEOUT_MS
                connection.readTimeout = READ_TIMEOUT_MS
                connection.setRequestProperty("User-Agent", USER_AGENT)
                connection.instanceFollowRedirects = false
                val code = connection.responseCode
                // GitHub serves release assets as a redirect to its object store,
                // and HttpURLConnection will not follow one across protocols or
                // hosts by itself. Followed by hand, and only to https.
                if (code in 300..399 && redirects < MAX_REDIRECTS) {
                    val next = connection.getHeaderField("Location") ?: break
                    require(next.startsWith("https://")) { "refusing a redirect to $next" }
                    connection.disconnect()
                    connection = URL(next).openConnection() as HttpURLConnection
                    redirects++
                    continue
                }
                if (code != HttpURLConnection.HTTP_OK) {
                    throw IllegalStateException("GitHub answered $code")
                }
                break
            }

            val total = connection.contentLength
            val buffer = ByteArray(64 * 1024)
            val sink = ByteArrayOutputStream(if (total > 0) total else 1 shl 20)
            connection.inputStream.use { input ->
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    sink.write(buffer, 0, read)
                    if (sink.size() > limit) throw IllegalStateException("the download is far larger than expected")
                    if (total > 0 && onProgress != null) {
                        onProgress((sink.size() * 100L / total).toInt().coerceIn(0, 100))
                    }
                }
            }
            return sink.toByteArray()
        } finally {
            connection.disconnect()
        }
    }

    private fun Exception.messageForUser(): String =
        message?.takeIf { it.isNotBlank() } ?: javaClass.simpleName

    companion object {
        private const val TAG = "NexusUpdater"
        private const val USER_AGENT = "NexusController-android"
        private const val SESSION_NAME = "nexus"
        private const val CONNECT_TIMEOUT_MS = 10_000
        private const val READ_TIMEOUT_MS = 30_000
        private const val MAX_REDIRECTS = 5
        private const val MAX_JSON_BYTES = 1 shl 20
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
            PackageInstaller.STATUS_SUCCESS -> report(UpdateStatus.Idle)
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

    private fun report(status: UpdateStatus) {
        listener?.invoke(status)
    }

    companion object {
        const val ACTION = "com.nexuscontroller.pad.INSTALL_RESULT"

        /**
         * Set by whatever screen started the install, so the outcome can be shown
         * there. A field rather than anything cleverer because the receiver is
         * created by the system, gets no constructor arguments, and this process
         * is alive throughout — the confirmation dialog belongs to the installer,
         * not to us, so nothing here is ever restored from a cold start.
         */
        @Volatile
        var listener: ((UpdateStatus) -> Unit)? = null
    }
}
