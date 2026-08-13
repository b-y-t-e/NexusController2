package com.nexuscontroller.pad

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The phone's half of in-app updating.
 *
 * The mirror of `tests/test_updates.py`: the same repository, the same rules
 * about which URL is ours, the same refusal to compare versions as text. Where
 * the two sides genuinely differ is the flavour — a release ships two APKs and
 * this side has to pick the one that will install on the phone it is running on.
 */
class UpdateCheckTest {

    private fun releaseJson(
        tag: String = "v2.1.0",
        assets: List<Pair<String, String>> = listOf(
            "NexusController.apk" to UpdateCheck.DOWNLOAD_PREFIX + "v2.1.0/NexusController.apk",
            "NexusController-legacy.apk" to UpdateCheck.DOWNLOAD_PREFIX + "v2.1.0/NexusController-legacy.apk",
            "SHA256SUMS.txt" to UpdateCheck.DOWNLOAD_PREFIX + "v2.1.0/SHA256SUMS.txt"
        )
    ): String {
        val entries = assets.joinToString(",") { (name, url) ->
            """{"name":"$name","browser_download_url":"$url"}"""
        }
        return """{"tag_name":"$tag","body":"notes","assets":[$entries]}"""
    }

    // --- versions ----------------------------------------------------------

    @Test
    fun `reads the version shapes this project produces`() {
        assertEquals(Triple(2, 1, 0), UpdateCheck.parseVersion("2.1.0"))
        assertEquals(Triple(2, 1, 0), UpdateCheck.parseVersion("v2.1.0"))
        assertEquals(Triple(2, 1, 0), UpdateCheck.parseVersion("  2.1.0 "))
        assertEquals(Triple(2, 1, 0), UpdateCheck.parseVersion("2.1"))
        assertEquals(Triple(3, 0, 0), UpdateCheck.parseVersion("3"))
    }

    @Test
    fun `the legacy suffix is not part of the number`() {
        // versionNameSuffix makes this build call itself "2.1.0-legacy"; compared
        // as text against "2.1.0" it would offer an update to the version already
        // installed, on every launch, forever.
        assertEquals(Triple(2, 1, 0), UpdateCheck.parseVersion("2.1.0-legacy"))
        assertFalse(UpdateCheck.isNewer("2.1.0-legacy", "2.1.0"))
    }

    @Test
    fun `refuses anything that is not a version`() {
        for (text in listOf("", "latest", "2.1.0.0", "two.one", "v", "2.x", null)) {
            assertNull("parsed $text", UpdateCheck.parseVersion(text))
        }
    }

    @Test
    fun `compares numerically and not as text`() {
        assertTrue(UpdateCheck.isNewer("2.9.0", "2.10.0"))
        assertFalse(UpdateCheck.isNewer("2.10.0", "2.9.0"))
    }

    @Test
    fun `the same version is not an update`() {
        assertFalse(UpdateCheck.isNewer("2.0.0", "2.0.0"))
    }

    @Test
    fun `an unreadable version is never newer`() {
        assertFalse(UpdateCheck.isNewer("2.0.0", "nightly"))
        assertFalse(UpdateCheck.isNewer("nightly", "2.0.0"))
    }

    // --- flavours ----------------------------------------------------------

    @Test
    fun `each flavour downloads its own apk`() {
        // The modern APK on an API 21 phone is INSTALL_FAILED_OLDER_SDK, and the
        // phone has no way to explain that to anybody.
        assertEquals(UpdateCheck.MODERN_ASSET, UpdateCheck.assetFor("modern"))
        assertEquals(UpdateCheck.LEGACY_ASSET, UpdateCheck.assetFor("legacy"))
        assertEquals(UpdateCheck.LEGACY_ASSET, UpdateCheck.assetFor("LEGACY"))
    }

    @Test
    fun `an unknown flavour gets the ordinary apk`() {
        assertEquals(UpdateCheck.MODERN_ASSET, UpdateCheck.assetFor(""))
    }

    // --- the release document ----------------------------------------------

    @Test
    fun `reads tag assets and notes`() {
        val release = UpdateCheck.parseRelease(releaseJson())
        assertNotNull(release)
        assertEquals("v2.1.0", release!!.tag)
        assertEquals("2.1.0", release.version)
        assertEquals("notes", release.notes)
        assertNotNull(release.url(UpdateCheck.MODERN_ASSET))
        assertNotNull(release.url(UpdateCheck.LEGACY_ASSET))
    }

    @Test
    fun `drops assets served from anywhere else`() {
        // The URL arrives inside a JSON document and decides where the bytes we
        // are about to install come from.
        val release = UpdateCheck.parseRelease(
            releaseJson(
                assets = listOf(
                    "NexusController.apk" to "https://example.invalid/NexusController.apk",
                    "SHA256SUMS.txt" to UpdateCheck.DOWNLOAD_PREFIX + "v2.1.0/SHA256SUMS.txt"
                )
            )
        )
        assertNull(release!!.url(UpdateCheck.MODERN_ASSET))
        assertNotNull(release.url(UpdateCheck.CHECKSUMS_ASSET))
    }

    @Test
    fun `a lookalike repository is not ours`() {
        val release = UpdateCheck.parseRelease(
            releaseJson(
                assets = listOf(
                    "NexusController.apk" to
                        "https://github.com/evil/NexusController2-x/releases/download/v9/NexusController.apk"
                )
            )
        )
        assertNull(release!!.url(UpdateCheck.MODERN_ASSET))
    }

    @Test
    fun `nothing usable is null rather than a crash`() {
        for (json in listOf(null, "", "   ", "not json", "[]", "{}", """{"tag_name":"latest"}""")) {
            assertNull("parsed $json", UpdateCheck.parseRelease(json))
        }
    }

    @Test
    fun `survives junk among the assets`() {
        val json = """{"tag_name":"v2.1.0","assets":[null,5,{"name":"x"},""" +
            """{"name":"SHA256SUMS.txt","browser_download_url":"${UpdateCheck.DOWNLOAD_PREFIX}v2.1.0/SHA256SUMS.txt"}]}"""
        val release = UpdateCheck.parseRelease(json)
        assertNotNull(release!!.url(UpdateCheck.CHECKSUMS_ASSET))
    }

    // --- checksums ---------------------------------------------------------

    @Test
    fun `reads the checksum file the release ships`() {
        val text = "a".repeat(64) + "  NexusController.apk\n" + "b".repeat(64) + "  NexusController.exe\n"
        val sums = UpdateCheck.parseChecksums(text)
        assertEquals("a".repeat(64), sums["NexusController.apk"])
        assertEquals("b".repeat(64), sums["NexusController.exe"])
    }

    @Test
    fun `accepts the binary mode star and ignores everything else`() {
        assertEquals(
            mapOf("NexusController.apk" to "c".repeat(64)),
            UpdateCheck.parseChecksums("# a comment\n\n" + "c".repeat(64) + " *NexusController.apk\nnot a hash  file")
        )
    }

    @Test
    fun `a matching payload passes`() {
        val payload = "an apk".toByteArray()
        val text = UpdateCheck.sha256(payload) + "  NexusController.apk"
        assertTrue(UpdateCheck.matchesChecksum(payload, "NexusController.apk", text))
    }

    @Test
    fun `a payload that does not match is refused`() {
        // A download cut short by the Wi-Fi dropping looks exactly like a
        // tampered one from here, and neither should be handed to the installer.
        val text = UpdateCheck.sha256("an apk".toByteArray()) + "  NexusController.apk"
        assertFalse(UpdateCheck.matchesChecksum("half an ap".toByteArray(), "NexusController.apk", text))
    }

    @Test
    fun `a payload nothing vouches for is refused`() {
        val payload = "an apk".toByteArray()
        assertFalse(UpdateCheck.matchesChecksum(payload, "NexusController.apk", ""))
        assertFalse(UpdateCheck.matchesChecksum(payload, "NexusController.apk", null))
    }

    @Test
    fun `sha256 is the same hex the release file uses`() {
        assertEquals(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            UpdateCheck.sha256(ByteArray(0))
        )
    }
}
