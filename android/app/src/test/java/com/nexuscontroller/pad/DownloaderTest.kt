package com.nexuscontroller.pad

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * The rules that decide whether bytes off the network are allowed to become an
 * installed app. They used to live behind `URL.openConnection()`, where only a
 * real GitHub release could reach them.
 */
class DownloaderTest {

    /** An HttpURLConnection that answers from memory. */
    private class FakeConnection(
        url: String,
        private val code: Int,
        private val body: ByteArray = ByteArray(0),
        private val location: String? = null,
        private val declaredLength: Int? = null,
    ) : HttpURLConnection(URL(url)) {
        var disconnected = false

        override fun getResponseCode() = code
        override fun getHeaderField(name: String): String? =
            if (name == "Location") location else null
        override fun getInputStream() = ByteArrayInputStream(body)
        override fun getContentLength() = declaredLength ?: body.size
        override fun connect() {}
        override fun disconnect() { disconnected = true }
        override fun usingProxy() = false
    }

    private val asset = UpdateCheck.DOWNLOAD_PREFIX + "v2.0.1/NexusController.apk"

    private fun downloader(vararg answers: FakeConnection): Pair<Downloader, MutableList<String>> {
        val asked = mutableListOf<String>()
        val queue = ArrayDeque(answers.toList())
        return Downloader { url ->
            asked += url
            queue.removeFirstOrNull() ?: throw AssertionError("no answer queued for $url")
        } to asked
    }

    @Test
    fun `a body is read whole`() {
        val (downloader, _) = downloader(FakeConnection(asset, 200, "payload".toByteArray()))
        assertEquals("payload", String(downloader.read(asset, 1024)))
    }

    @Test
    fun `a redirect to https is followed`() {
        val next = UpdateCheck.DOWNLOAD_PREFIX + "v2.0.1/elsewhere.apk"
        val (downloader, asked) = downloader(
            FakeConnection(asset, 302, location = next),
            FakeConnection(next, 200, "moved".toByteArray()),
        )
        assertEquals("moved", String(downloader.read(asset, 1024)))
        assertEquals(listOf(asset, next), asked)
    }

    @Test
    fun `a redirect to plain http is refused`() {
        """These bytes become an installed app. A downgrade to http would put a
        stranger in the middle of that, and the checksum is no defence because it
        travels the same way."""
        val (downloader, _) = downloader(
            FakeConnection(asset, 302, location = "http://evil.example/app.apk")
        )
        try {
            downloader.read(asset, 1024)
            fail("a plain-http redirect must not be followed")
        } catch (e: IllegalArgumentException) {
            assertTrue(e.message!!.contains("refusing a redirect"))
        }
    }

    @Test
    fun `a url that is not ours is refused before anything is opened`() {
        val (downloader, asked) = downloader()
        try {
            downloader.read("https://example.com/whatever.apk", 1024)
            fail("only our own release URLs may be fetched")
        } catch (e: IllegalArgumentException) {
            assertTrue(e.message!!.contains("refusing to download"))
        }
        assertEquals(emptyList<String>(), asked)
    }

    @Test
    fun `a body larger than the cap is refused rather than swallowed`() {
        val (downloader, _) = downloader(FakeConnection(asset, 200, ByteArray(4096)))
        try {
            downloader.read(asset, 1024)
            fail("the cap must hold")
        } catch (e: IllegalStateException) {
            assertTrue(e.message!!.contains("larger than expected"))
        }
    }

    @Test
    fun `an error response is an error`() {
        val (downloader, _) = downloader(FakeConnection(asset, 503))
        try {
            downloader.read(asset, 1024)
            fail("503 is not a download")
        } catch (e: IllegalStateException) {
            assertTrue(e.message!!.contains("503"))
        }
    }

    @Test
    fun `streaming to a file reports each percentage once`() {
        """The read loop turns over every 64 KB. For an APK that is hundreds of
        calls, most of them repeating the last number — and each one hops to the
        main thread to move a progress bar by nothing."""
        val target = File.createTempFile("nexus-test", ".apk")
        target.deleteOnExit()
        val body = ByteArray(300 * 1024) { it.toByte() }
        val (downloader, _) = downloader(FakeConnection(asset, 200, body))

        val seen = mutableListOf<Int>()
        downloader.readTo(asset, target, body.size + 1) { seen += it }

        assertEquals(body.size.toLong(), target.length())
        assertEquals("every report should be a new number", seen.distinct(), seen)
        assertTrue("progress should reach the end", seen.last() == 100)
    }

    @Test
    fun `a connection is always disconnected`() {
        val connection = FakeConnection(asset, 200, "x".toByteArray())
        val downloader = Downloader { connection }
        downloader.read(asset, 1024)
        assertTrue(connection.disconnected)
    }
}
