package com.nexuscontroller.pad

import java.io.File
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL

/**
 * Fetching, with the rules that make it safe to install what comes back.
 *
 * Separated from [Updater] so those rules can be tested: what to do with a
 * redirect, what to do with a body that will not stop, and how often the caller
 * hears about progress. All three used to sit inside a private method behind
 * `URL.openConnection()`, where nothing but a real network could reach them.
 *
 * [connect] is the seam. Its default is the real thing.
 */
class Downloader(
    private val connect: (String) -> HttpURLConnection = {
        URL(it).openConnection() as HttpURLConnection
    }
) {

    /** Read a whole response into memory. Only for small, known-small documents. */
    fun read(url: String, limit: Int): ByteArray {
        val sink = java.io.ByteArrayOutputStream()
        open(url) { connection ->
            copy(connection, sink, limit, total = connection.contentLength, onProgress = null)
        }
        return sink.toByteArray()
    }

    /**
     * Stream a response into [target].
     *
     * [onProgress] is called with whole percentages and only when the number
     * actually changes: the read loop turns over every 64 KB, which for an APK
     * of any size is hundreds of calls saying what the last one said, each one
     * hopping to the main thread to move a progress bar by nothing.
     */
    fun readTo(url: String, target: File, limit: Int, onProgress: ((Int) -> Unit)? = null) {
        target.outputStream().use { out ->
            open(url) { connection ->
                copy(connection, out, limit, connection.contentLength, onProgress)
            }
        }
    }

    private fun <T> open(url: String, use: (HttpURLConnection) -> T): T {
        // Ours or nothing. The API URL is a constant and every asset URL was
        // checked against DOWNLOAD_PREFIX when the release was parsed, so this is
        // belt and braces — but it is the last point before bytes become an app.
        require(url.startsWith(UpdateCheck.DOWNLOAD_PREFIX) || url == UpdateCheck.RELEASE_API) {
            "refusing to download from $url"
        }
        var connection = connect(url)
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
                // hosts by itself. Followed by hand, and only to https — these
                // bytes become an installed app, and a downgrade to plain http
                // would put a stranger in the middle of that.
                if (code in 300..399) {
                    val next = connection.getHeaderField("Location")
                        ?: throw IllegalStateException("GitHub answered $code with no Location")
                    require(redirects < MAX_REDIRECTS) { "too many redirects" }
                    require(next.startsWith("https://")) { "refusing a redirect to $next" }
                    connection.disconnect()
                    connection = connect(next)
                    redirects++
                    continue
                }
                if (code != HttpURLConnection.HTTP_OK) {
                    throw IllegalStateException("GitHub answered $code")
                }
                break
            }
            return use(connection)
        } finally {
            connection.disconnect()
        }
    }

    private fun copy(
        connection: HttpURLConnection,
        out: OutputStream,
        limit: Int,
        total: Int,
        onProgress: ((Int) -> Unit)?
    ) {
        val buffer = ByteArray(COPY_BUFFER)
        var written = 0L
        var reported = -1
        connection.inputStream.use { input ->
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                written += read
                if (written > limit) {
                    throw IllegalStateException("the download is far larger than expected")
                }
                out.write(buffer, 0, read)
                if (total > 0 && onProgress != null) {
                    val percent = (written * 100L / total).toInt().coerceIn(0, 100)
                    if (percent != reported) {
                        reported = percent
                        onProgress(percent)
                    }
                }
            }
        }
    }

    private companion object {
        const val USER_AGENT = "NexusController-android"
        const val CONNECT_TIMEOUT_MS = 10_000
        const val READ_TIMEOUT_MS = 30_000
        const val MAX_REDIRECTS = 5
        const val COPY_BUFFER = 64 * 1024
    }
}
