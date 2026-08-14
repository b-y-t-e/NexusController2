package com.nexuscontroller.pad

import java.security.MessageDigest

/**
 * Everything about updating that can be decided without a network or a phone.
 *
 * The mirror of `server/nexus_server/updates.py`: same repository, same asset
 * names, same rules about which URL is ours and what a version is. `Updater`
 * holds the half that opens sockets and talks to the package installer; this
 * half takes text and returns answers, so it is tested like any other pure code.
 *
 * The one thing this side has to get right that the PC side does not: **the
 * flavour**. A release ships two APKs, and installing the `modern` one on a
 * phone below API 28 fails with `INSTALL_FAILED_OLDER_SDK` — so the asset is
 * chosen from what this build *is*, never from what is newest.
 */
data class ReleaseInfo(
    val tag: String,
    /** `name -> download URL`, already filtered to URLs from our own releases. */
    val assets: Map<String, String>,
    val notes: String = ""
) {
    val version: String get() = tag.trimStart('v', 'V')

    fun url(name: String): String? = assets[name]
}

object UpdateCheck {

    const val REPO = "b-y-t-e/NexusController2"
    const val RELEASE_API = "https://api.github.com/repos/$REPO/releases/latest"
    const val RELEASES_PAGE = "https://github.com/$REPO/releases/latest"

    /**
     * Every asset of a real release of ours is served from under this. Checked
     * before anything is fetched, because the URL arrives inside a JSON document
     * and decides where the bytes we are about to *install* come from.
     */
    const val DOWNLOAD_PREFIX = "https://github.com/$REPO/releases/download/"

    const val MODERN_ASSET = "NexusController.apk"
    const val LEGACY_ASSET = "NexusController-legacy.apk"
    const val CHECKSUMS_ASSET = "SHA256SUMS.txt"

    /** Digits allowed in one part of a version; the Python side agrees. */
    const val MAX_VERSION_DIGITS = 9

    /** The APK that belongs to this build. See the note on flavours above. */
    fun assetFor(flavor: String): String =
        if (flavor.equals("legacy", ignoreCase = true)) LEGACY_ASSET else MODERN_ASSET

    /**
     * `"v2.1.0"`, `"2.1"` and `"2.1.0-legacy"` all read as a version; anything
     * else reads as null. The suffix matters here: the legacy flavour really does
     * call itself `2.1.0-legacy`, and comparing that as text against `2.1.0`
     * would offer an update to the version already installed, every time.
     */
    fun parseVersion(text: String?): Triple<Int, Int, Int>? {
        if (text == null) return null
        val cleaned = text.trim().trimStart('v', 'V').substringBefore('-').substringBefore('+')
        // split() never returns an empty list — "" splits to [""], which the
        // digit check below rejects — so the only thing to refuse here is a
        // fourth part.
        val parts = cleaned.split('.')
        if (parts.size > 3) return null
        val numbers = parts.map { part ->
            // '0'..'9' and not isDigit(): the latter is true for every Unicode
            // decimal digit, and toIntOrNull() duly reads "٣" as 3 — so a tag in
            // Arabic-Indic numerals would compare as a version. The Python side
            // spells the same range out for the mirror-image reason.
            // The length cap is the other half of the same agreement: Python's
            // int has no ceiling, so without it "9999999999.0.0" is a version
            // there and null here. Nine digits is more than any real one needs.
            if (part.isEmpty() || part.length > MAX_VERSION_DIGITS) return null
            if (!part.all { it in '0'..'9' }) return null
            part.toIntOrNull() ?: return null
        }
        return Triple(
            numbers[0],
            numbers.getOrElse(1) { 0 },
            numbers.getOrElse(2) { 0 }
        )
    }

    /**
     * Whether [candidate] is worth offering over [current].
     *
     * Compared number by number, never as text: "2.10.0" sorts before "2.9.0"
     * alphabetically, which would offer a downgrade — and keep offering it.
     * A version neither side can read is never newer; not knowing is not a reason
     * to replace the app someone is using.
     */
    fun isNewer(current: String?, candidate: String?): Boolean {
        val here = parseVersion(current) ?: return false
        val there = parseVersion(candidate) ?: return false
        return compareValuesBy(there, here, { it.first }, { it.second }, { it.third }) > 0
    }

    /**
     * Reads GitHub's `releases/latest` answer. Null means "nothing usable here" —
     * which includes the ordinary case of a repository with no releases yet.
     */
    fun parseRelease(json: String?): ReleaseInfo? {
        if (json.isNullOrBlank()) return null
        val root = try {
            MiniJson.parseObject(json)
        } catch (e: Exception) {
            return null
        }
        val tag = root["tag_name"] as? String ?: return null
        if (parseVersion(tag) == null) return null

        val assets = mutableMapOf<String, String>()
        val listed = root["assets"] as? List<*> ?: emptyList<Any?>()
        for (entry in listed) {
            @Suppress("UNCHECKED_CAST")
            val asset = entry as? Map<String, Any?> ?: continue
            val name = asset["name"] as? String ?: continue
            val url = asset["browser_download_url"] as? String ?: continue
            // Dropped here rather than at download time, so no later step has to
            // remember to look.
            if (url.startsWith(DOWNLOAD_PREFIX)) assets[name] = url
        }
        return ReleaseInfo(tag = tag, assets = assets, notes = root["body"] as? String ?: "")
    }

    /** Reads `SHA256SUMS.txt` — `<hex>  <name>` per line. */
    fun parseChecksums(text: String?): Map<String, String> {
        val sums = mutableMapOf<String, String>()
        for (line in (text ?: "").lineSequence()) {
            val parts = line.trim().split(Regex("\\s+"))
            // "*name" is how sha256sum marks binary mode; the name is what matters.
            if (parts.size == 2 && parts[0].length == 64) {
                sums[parts[1].removePrefix("*")] = parts[0].lowercase()
            }
        }
        return sums
    }

    fun sha256(payload: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(payload)
            .joinToString("") { "%02x".format(it) }

    /**
     * Whether a download is the file the release says it is.
     *
     * Not a defence against a compromised release — the list ships beside what it
     * describes — but it catches what actually happens on a phone: a download cut
     * short by the Wi-Fi dropping, or a captive portal answering with its login
     * page. This APK is about to be handed to the package installer, so "probably
     * fine" is not a standard it can be held to.
     */
    fun matchesChecksum(payload: ByteArray, name: String, checksums: String?): Boolean =
        matchesChecksum(sha256(payload), name, checksums)

    /**
     * The same question for a file too big to hold in memory.
     *
     * The APK is streamed to disk and hashed as it goes, so what arrives here is
     * the digest rather than the bytes — see [Updater.downloadAndInstall].
     */
    fun matchesChecksum(digest: String, name: String, checksums: String?): Boolean {
        val expected = parseChecksums(checksums)[name] ?: return false
        return digest.equals(expected, ignoreCase = true)
    }
}


/**
 * Who is allowed to write the status the update screen is showing.
 *
 * Two things produce one. The version check and the download report progress and
 * results by returning from a suspending call; the *installer* reports through a
 * broadcast, because the confirmation dialog belongs to the system. The second
 * one routinely arrives first — `downloadAndInstall` returns `Installing` as
 * soon as the session is committed, and the user's answer to the dialog, most
 * often "no", comes back while that line is still on its way. Writing it blindly
 * put "Installing…" back over "Update cancelled" with nothing left to change it,
 * and the same thing happened after a rotation: the outcome was replayed to the
 * new screen and a routine version check wrote "you are up to date" over it —
 * which is not even true until the app restarts.
 *
 * So: an outcome from the installer wins and *keeps* winning, until the user
 * asks for something new by hand. Not Compose-aware on purpose — this is the
 * rule, and it is tested as one.
 *
 * **Its life is one composition.** It is held by `remember`, not
 * `rememberSaveable`, and it guards a status that is not saveable either: a
 * configuration change takes the whole screen back to `Idle` and runs a fresh
 * check, which is the right answer for everything except an outcome the user was
 * reading at that moment. That case is covered from the other end —
 * [InstallResultReceiver] replays an undelivered outcome to the next screen for
 * a minute — and closing the remaining gap properly means a ViewModel holding
 * the status, not a saveable flag guarding one that is already gone.
 */
class UpdateScreenState {
    /**
     * Volatile because the two sides are on different threads: the broadcast and
     * the taps arrive on the main thread, while `downloadAndInstall` runs its
     * progress callback from inside `withContext(Dispatchers.IO)`. Without it
     * the worker may go on reading a stale `false` for as long as the JVM likes,
     * which is precisely the window this class exists to close.
     */
    @Volatile
    var settled: Boolean = false
        private set

    /** An outcome from the system installer. Always shown, and it sticks. */
    fun fromInstaller() {
        settled = true
    }

    /** A result from a check or a download. Shown unless an outcome is standing. */
    fun fromWork(): Boolean = !settled

    /** The user pressed something: whatever is on screen is being replaced on purpose. */
    fun userAsked() {
        settled = false
    }
}
