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
        val parts = cleaned.split('.')
        if (parts.isEmpty() || parts.size > 3) return null
        val numbers = parts.map { part ->
            if (part.isEmpty() || !part.all { it.isDigit() }) return null
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
    fun matchesChecksum(payload: ByteArray, name: String, checksums: String?): Boolean {
        val expected = parseChecksums(checksums)[name] ?: return false
        return sha256(payload) == expected
    }
}
