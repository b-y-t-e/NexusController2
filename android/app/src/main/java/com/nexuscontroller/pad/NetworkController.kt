package com.nexuscontroller.pad

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.InputStream
import java.io.OutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger

/**
 * TCP client for protocol v2.
 *
 * One [Connection] object per dial attempt: it owns its own child [CoroutineScope], its own
 * channels and its own socket, and everything it owns dies together in [Connection.close].
 * A monotonically increasing generation counter decides which attempt is "current", so a
 * late teardown of an old attempt can never report DISCONNECTED over a newer, live one.
 *
 * USB mode is just `adb reverse tcp:6000 tcp:6000` on the PC — the client dials 127.0.0.1
 * like any other host. There is no listening socket on the phone any more.
 */
class NetworkController {

    enum class State { DISCONNECTED, CONNECTING, CONNECTED, ERROR }

    var onStateChanged: ((State) -> Unit)? = null
    var onLatencyUpdate: ((Long) -> Unit)? = null
    var onRumble: ((Int, Int) -> Unit)? = null
    var onLed: ((Int, Int, Int) -> Unit)? = null
    var onError: ((String) -> Unit)? = null
    var onWelcome: ((Welcome) -> Unit)? = null
    /** Fired when the server explicitly refuses the handshake. */
    var onRejected: ((RejectReason?) -> Unit)? = null
    /**
     * `0x13 SET_CONFIG` — the PC pushing a configuration document (§10). The raw JSON is
     * handed over on the **main** thread, so the callback may touch Compose state directly.
     */
    var onSetConfig: ((String) -> Unit)? = null

    private val rootScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private val generation = AtomicInteger(0)

    @Volatile
    private var current: Connection? = null

    @Volatile
    var state: State = State.DISCONNECTED
        private set

    val isConnected: Boolean get() = state == State.CONNECTED
    val isBusy: Boolean get() = state == State.CONNECTED || state == State.CONNECTING

    /** Everything one dial attempt owns. */
    private inner class Connection(val id: Int, val deviceType: ControllerType) {
        val job = SupervisorJob(rootScope.coroutineContext[Job])
        val scope = CoroutineScope(Dispatchers.IO + job)

        /** Conflated: if the network lags we send the newest pad state, never a backlog. */
        val inputChannel = Channel<ByteArray>(Channel.CONFLATED)
        val otherChannel = Channel<ByteArray>(Channel.UNLIMITED)
        val writeMutex = Mutex()
        val pingSentAt = ConcurrentHashMap<Long, Long>()

        @Volatile var socket: Socket? = null
        @Volatile var output: OutputStream? = null
        @Volatile var input: InputStream? = null

        val isCurrent: Boolean get() = generation.get() == id

        /** Teardown must be idempotent: reader and writer can both fail on the same socket. */
        val finished = java.util.concurrent.atomic.AtomicBoolean(false)

        fun close() {
            try {
                socket?.close()
            } catch (e: Exception) {
                Log.w(TAG, "socket close failed", e)
            }
            socket = null
            output = null
            input = null
            inputChannel.close()
            otherChannel.close()
            scope.cancel()
        }
    }

    // ---------------------------------------------------------------- connecting

    /**
     * Dials [target] (null means USB / `127.0.0.1`). Always tears down whatever came before,
     * so an explicit user connect can never be blocked by a stale attempt.
     */
    fun connect(
        target: ConnectionTarget?,
        deviceType: ControllerType,
        deviceName: String
    ) {
        val dest = target ?: ConnectionTarget("127.0.0.1", Protocol.DEFAULT_PORT, "")
        // The cursor state belongs to a connection, not to this object. The
        // server lets go of every button a client was holding when it goes away,
        // so a `lastMouseButtons` left over from the old session would suppress
        // the message that presses it again — a selection interrupted by a
        // reconnect would come back with the button believed down on one side
        // and up on the other.
        mouseAccX = 0f
        mouseAccY = 0f
        lastMouseButtons = -1
        val id = generation.incrementAndGet()
        current?.close()
        val conn = Connection(id, deviceType)
        current = conn
        notifyState(State.CONNECTING, conn)

        conn.scope.launch { runConnection(conn, dest, deviceName) }
    }

    /** Used by the auto-reconnect loop: does nothing while a connection is live or pending. */
    fun connectIfIdle(target: ConnectionTarget?, deviceType: ControllerType, deviceName: String) {
        if (isBusy) return
        connect(target, deviceType, deviceName)
    }

    private suspend fun runConnection(
        conn: Connection,
        target: ConnectionTarget,
        deviceName: String
    ) {
        var reportedError: String? = null
        try {
            Log.d(TAG, "connecting to ${target.ip}:${target.port} as ${conn.deviceType}")
            val socket = Socket()
            conn.socket = socket
            socket.connect(InetSocketAddress(target.ip, target.port), CONNECT_TIMEOUT_MS)
            socket.tcpNoDelay = true
            socket.soTimeout = HANDSHAKE_TIMEOUT_MS
            val out = socket.getOutputStream()
            val inp = socket.getInputStream()
            conn.output = out
            conn.input = inp

            out.write(Protocol.hello(conn.deviceType, target.token, deviceName))
            out.flush()

            when (val opcode = inp.read()) {
                Protocol.OP_WELCOME -> {
                    val payload = readFully(inp, 2)
                    val welcome = Protocol.parseWelcome(payload[0].toInt(), payload[1].toInt())
                    Log.d(TAG, "WELCOME slot=${welcome.slot} features=${welcome.features}")
                    main { onWelcome?.invoke(welcome) }
                }
                Protocol.OP_REJECT -> {
                    val reasonCode = inp.read()
                    if (reasonCode < 0) throw ProtocolException("Connection closed during handshake")
                    val reason = RejectReason.fromCode(reasonCode)
                    main { onRejected?.invoke(reason) }
                    // A code, not a sentence: this file has no Context and the text is
                    // translated. MainActivity turns it into something readable.
                    throw ProtocolException("$REJECT_PREFIX$reasonCode")
                }
                -1 -> throw ProtocolException("Server closed the connection during the handshake")
                else -> throw ProtocolException("Unexpected handshake reply 0x%02X".format(opcode))
            }

            socket.soTimeout = 0
            startWriters(conn)
            startPingLoop(conn)
            notifyState(State.CONNECTED, conn)
            readLoop(conn, inp)
        } catch (e: Exception) {
            if (conn.isCurrent) {
                reportedError = e.message ?: "Connection lost"
                Log.e(TAG, "connection error: $reportedError", e)
            } else {
                Log.d(TAG, "ignored error from stale connection ${conn.id}: ${e.message}")
            }
        } finally {
            finish(conn, reportedError)
        }
    }

    private suspend fun readLoop(conn: Connection, inp: InputStream) {
        while (conn.scope.isActive) {
            when (val opcode = inp.read()) {
                -1 -> throw ProtocolException("Server closed the connection")
                Protocol.OP_RUMBLE -> {
                    val p = readFully(inp, 2)
                    val large = p[0].toInt() and 0xFF
                    val small = p[1].toInt() and 0xFF
                    main { onRumble?.invoke(large, small) }
                }
                Protocol.OP_LED -> {
                    val p = readFully(inp, 3)
                    main {
                        onLed?.invoke(p[0].toInt() and 0xFF, p[1].toInt() and 0xFF, p[2].toInt() and 0xFF)
                    }
                }
                Protocol.OP_PONG -> {
                    val p = readFully(inp, 4)
                    val seq = Protocol.readUInt32(p, 0)
                    val sentAt = conn.pingSentAt.remove(seq)
                    if (sentAt != null) {
                        val latency = System.currentTimeMillis() - sentAt
                        main { onLatencyUpdate?.invoke(latency) }
                    } else {
                        Log.w(TAG, "PONG for unknown seq $seq")
                    }
                }
                Protocol.OP_WELCOME -> {
                    val p = readFully(inp, 2)
                    main { onWelcome?.invoke(Protocol.parseWelcome(p[0].toInt(), p[1].toInt())) }
                }
                Protocol.OP_SET_CONFIG -> {
                    // Length-prefixed, so an over-long or unusable body can be skipped without
                    // desynchronising the stream — this one is not fatal.
                    val header = readFully(inp, 2)
                    val length = Protocol.readUInt16(header, 0)
                    val body = readFully(inp, length)
                    if (length > Protocol.MAX_CONFIG_BYTES) {
                        Log.w(TAG, "SET_CONFIG of $length bytes exceeds the protocol limit, ignored")
                    } else {
                        val json = Protocol.decodeConfigBody(body)
                        Log.d(TAG, "SET_CONFIG received, $length bytes")
                        main { onSetConfig?.invoke(json) }
                    }
                }
                Protocol.OP_REJECT -> {
                    val reasonCode = inp.read()
                    val reason = if (reasonCode >= 0) RejectReason.fromCode(reasonCode) else null
                    main { onRejected?.invoke(reason) }
                    throw ProtocolException(
                        if (reasonCode >= 0) "$REJECT_PREFIX$reasonCode" else REJECT_PREFIX
                    )
                }
                // Payload lengths are fixed per opcode, so an unknown opcode desynchronises
                // the stream: the protocol says this is fatal.
                else -> throw ProtocolException("Unknown opcode 0x%02X from server".format(opcode))
            }
        }
    }

    /** Reads exactly [count] bytes or throws; `InputStream.read` may return short reads. */
    private fun readFully(inp: InputStream, count: Int): ByteArray {
        val buf = ByteArray(count)
        var read = 0
        while (read < count) {
            val n = inp.read(buf, read, count - read)
            if (n < 0) throw ProtocolException("Server closed the connection")
            read += n
        }
        return buf
    }

    private fun startWriters(conn: Connection) {
        // High priority: latest pad state only.
        conn.scope.launch {
            for (packet in conn.inputChannel) {
                if (!writePacket(conn, packet, flush = true)) return@launch
            }
        }
        // Everything else, batched so a mouse drag does not turn into a flush storm.
        conn.scope.launch {
            for (packet in conn.otherChannel) {
                val batch = ArrayList<ByteArray>(BATCH_LIMIT)
                batch.add(packet)
                while (batch.size < BATCH_LIMIT) {
                    batch.add(conn.otherChannel.tryReceive().getOrNull() ?: break)
                }
                if (!writeBatch(conn, batch)) return@launch
            }
        }
    }

    /** Returns false when the write failed and the connection was torn down. */
    private suspend fun writePacket(conn: Connection, packet: ByteArray, flush: Boolean): Boolean =
        writeBatch(conn, listOf(packet), flush)

    private suspend fun writeBatch(
        conn: Connection,
        packets: List<ByteArray>,
        flush: Boolean = true
    ): Boolean {
        return try {
            conn.writeMutex.withLock {
                val out = conn.output ?: throw ProtocolException("Not connected")
                packets.forEach { out.write(it) }
                if (flush) out.flush()
            }
            true
        } catch (e: Exception) {
            Log.e(TAG, "write failed, dropping connection ${conn.id}", e)
            if (conn.isCurrent) {
                main { onError?.invoke(e.message ?: "Send failed") }
            }
            finish(conn, null)
            false
        }
    }

    private fun startPingLoop(conn: Connection) {
        conn.scope.launch {
            var seq = 1L
            while (conn.scope.isActive) {
                delay(PING_INTERVAL_MS)
                val s = seq++ and 0xFFFFFFFFL
                conn.pingSentAt[s] = System.currentTimeMillis()
                // Drop stale entries so an unanswered ping cannot leak memory.
                if (conn.pingSentAt.size > 32) {
                    val cutoff = System.currentTimeMillis() - 30_000
                    conn.pingSentAt.entries.removeAll { it.value < cutoff }
                }
                conn.otherChannel.trySend(Protocol.ping(s))
            }
        }
    }

    /**
     * Tears down [conn]. Only an attempt that still owns the current generation may report
     * an error or flip the UI to DISCONNECTED — otherwise a slow teardown would stomp a
     * connect that has already succeeded.
     */
    private fun finish(conn: Connection, errorMessage: String?) {
        if (!conn.finished.compareAndSet(false, true)) return
        val owned = conn.isCurrent
        if (owned) {
            current = null
            if (errorMessage != null) {
                main { onError?.invoke(errorMessage) }
            }
            notifyState(State.DISCONNECTED, conn, force = true)
        }
        conn.close()
    }

    fun disconnect() {
        generation.incrementAndGet()   // invalidate whatever is running
        val conn = current
        current = null
        conn?.close()
        state = State.DISCONNECTED
        main { onStateChanged?.invoke(State.DISCONNECTED) }
    }

    private fun notifyState(newState: State, conn: Connection, force: Boolean = false) {
        if (!force && !conn.isCurrent) return
        state = newState
        main { onStateChanged?.invoke(newState) }
    }

    private fun main(block: () -> Unit) {
        rootScope.launch { withContext(Dispatchers.Main) { block() } }
    }

    // ---------------------------------------------------------------- sending

    /**
     * Sticks are the app's `0..255` values (127 = centre, Y growing downwards); the wire
     * conversion and the Buzz sanitisation live in [Protocol].
     */
    fun sendInput(
        lx: Int, ly: Int, rx: Int, ry: Int,
        btnsLow: Int, btnsHigh: Int,
        lt: Int, rt: Int,
        roll: Int = 0, pitch: Int = 0,
        mouseMode: Boolean = false,
        gyroValid: Boolean = false
    ) {
        val conn = current ?: return
        if (state != State.CONNECTED) return
        val flags = (if (mouseMode) Protocol.FLAG_MOUSE_MODE else 0) or
            (if (gyroValid) Protocol.FLAG_GYRO_VALID else 0)
        conn.inputChannel.trySend(
            Protocol.input(
                conn.deviceType, lx, ly, rx, ry, btnsLow, btnsHigh, lt, rt, roll, pitch, flags
            )
        )
    }

    fun sendText(text: String) {
        if (text.isEmpty()) return
        current?.otherChannel?.trySend(Protocol.text(text))
    }

    /**
     * `0x06 CONFIG` — tells the PC what this pad currently looks like (§10).
     * A document that does not fit the protocol's 16 KiB body is dropped with a log rather
     * than truncated: half a layout is worse than none.
     */
    fun sendConfig(json: String) {
        val conn = current ?: return
        val packet = Protocol.configJsonOrNull(json)
        if (packet == null) {
            Log.w(TAG, "CONFIG document too large (${json.length} chars), not sent")
            return
        }
        conn.otherChannel.trySend(packet)
    }

    private var mouseAccX = 0f
    private var mouseAccY = 0f
    private var lastMouseButtons = -1

    fun sendMouse(dx: Float, dy: Float, left: Boolean, right: Boolean, sensitivity: Float = 1.0f) {
        val conn = current ?: return
        mouseAccX += dx * sensitivity
        mouseAccY += dy * sensitivity
        val ix = mouseAccX.toInt()
        val iy = mouseAccY.toInt()
        val buttons = (if (left) 1 else 0) or (if (right) 2 else 0)
        if (ix != 0 || iy != 0 || buttons != lastMouseButtons) {
            conn.otherChannel.trySend(Protocol.mouse(ix, iy, buttons))
            mouseAccX -= ix
            mouseAccY -= iy
            lastMouseButtons = buttons
        }
    }

    private var scrollAccX = 0f
    private var scrollAccY = 0f

    fun sendScroll(dx: Float, dy: Float, sensitivity: Float = 1.0f) {
        val conn = current ?: return
        scrollAccX += dx * sensitivity
        scrollAccY += dy * sensitivity
        val ix = scrollAccX.toInt()
        val iy = scrollAccY.toInt()
        if (ix != 0 || iy != 0) {
            conn.otherChannel.trySend(Protocol.scroll(ix, iy))
            scrollAccX -= ix
            scrollAccY -= iy
        }
    }

    // ---------------------------------------------------------------- discovery

    @Volatile
    private var isDiscovering = false

    /** Broadcasts the v2 discovery request and reports every well-formed v2 reply. */
    fun startDiscovery(onFound: (ip: String, server: DiscoveredServer) -> Unit) {
        if (isDiscovering) return
        isDiscovering = true
        rootScope.launch {
            var ds: DatagramSocket? = null
            try {
                ds = DatagramSocket()
                ds.broadcast = true
                ds.soTimeout = DISCOVERY_TIMEOUT_MS

                val request = Protocol.DISCOVERY_REQUEST.toByteArray(Charsets.US_ASCII)
                val packet = DatagramPacket(
                    request, request.size,
                    InetAddress.getByName("255.255.255.255"), Protocol.DISCOVERY_PORT
                )

                while (isDiscovering) {
                    try {
                        ds.send(packet)
                        val buf = ByteArray(1024)
                        val response = DatagramPacket(buf, buf.size)
                        ds.receive(response)
                        val text = String(response.data, 0, response.length, Charsets.UTF_8)
                        val server = Protocol.parseDiscoveryResponse(text)
                        val ip = response.address?.hostAddress
                        if (server != null && ip != null) {
                            withContext(Dispatchers.Main) { onFound(ip, server) }
                        } else {
                            Log.d(TAG, "ignoring discovery reply: $text")
                        }
                    } catch (e: java.net.SocketTimeoutException) {
                        // no server answered this round, keep polling
                    } catch (e: Exception) {
                        Log.w(TAG, "discovery round failed", e)
                    }
                    delay(DISCOVERY_INTERVAL_MS)
                }
            } catch (e: Exception) {
                Log.e(TAG, "discovery failed", e)
                main { onError?.invoke(e.message ?: "Discovery failed") }
            } finally {
                ds?.close()
                isDiscovering = false
            }
        }
    }

    fun stopDiscovery() {
        isDiscovering = false
    }

    class ProtocolException(message: String) : Exception(message)

    companion object {
        /** Marker for "the server refused us, with this code". */
        const val REJECT_PREFIX = "reject:"

        private const val TAG = "Nexus"
        private const val CONNECT_TIMEOUT_MS = 5000
        private const val HANDSHAKE_TIMEOUT_MS = 5000
        private const val PING_INTERVAL_MS = 1000L
        private const val DISCOVERY_TIMEOUT_MS = 1500
        private const val DISCOVERY_INTERVAL_MS = 1000L
        private const val BATCH_LIMIT = 20
    }
}
