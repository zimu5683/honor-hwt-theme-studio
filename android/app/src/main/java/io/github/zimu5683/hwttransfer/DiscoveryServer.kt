package io.github.zimu5683.hwttransfer

import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

class DiscoveryServer(
    private val pairing: PairingManager,
    private val bindPort: Int = Protocol.DISCOVERY_PORT,
    private val socketFactory: () -> DatagramSocket = { DatagramSocket(null) },
) {
    init {
        require(bindPort in 0..65535) { "Discovery port must be between 0 and 65535" }
    }

    private val lock = Any()
    @Volatile private var running = false
    @Volatile private var socket: DatagramSocket? = null
    private var workerThread: Thread? = null
    private var executor: ExecutorService? = null

    internal val isRunning: Boolean
        get() = synchronized(lock) { running && executor != null }

    internal val localPort: Int
        get() = synchronized(lock) { socket?.localPort ?: -1 }

    fun start() {
        val worker: ExecutorService
        val ready = CountDownLatch(1)
        val startupFailure = AtomicReference<Throwable?>(null)
        synchronized(lock) {
            if (running) return
            running = true
            worker = Executors.newSingleThreadExecutor()
            executor = worker
        }
        try {
            worker.execute { serve(worker, ready, startupFailure) }
        } catch (exc: Exception) {
            synchronized(lock) {
                if (executor === worker) {
                    executor = null
                    running = false
                }
            }
            worker.shutdownNow()
            throw exc
        }
        try {
            if (!ready.await(STARTUP_TIMEOUT_MS, TimeUnit.MILLISECONDS)) {
                stopCurrent(worker)
                throw IOException("UDP discovery server startup timed out")
            }
        } catch (exc: InterruptedException) {
            Thread.currentThread().interrupt()
            stopCurrent(worker)
            throw IOException("UDP discovery server startup was interrupted", exc)
        }
        startupFailure.get()?.let { failure ->
            stopCurrent(worker)
            throw IOException("UDP discovery server failed to bind", failure)
        }
        val started = synchronized(lock) {
            running && executor === worker && socket != null
        }
        if (!started) {
            stopCurrent(worker)
            throw IOException("UDP discovery server stopped during startup")
        }
    }

    private fun serve(
        worker: ExecutorService,
        ready: CountDownLatch,
        startupFailure: AtomicReference<Throwable?>,
    ) {
        var server: DatagramSocket? = null
        var bound = false
        synchronized(lock) {
            if (executor === worker) workerThread = Thread.currentThread()
        }
        try {
            if (!owns(worker)) return
            val boundServer = socketFactory()
            server = boundServer
            boundServer.apply {
                reuseAddress = true
                broadcast = true
                soTimeout = 1000
                bind(InetSocketAddress(bindPort))
            }
            synchronized(lock) {
                if (executor !== worker || !running) return
                socket = boundServer
            }
            bound = true
            ready.countDown()
            val buffer = ByteArray(2048)
            while (owns(worker)) {
                val request = DatagramPacket(buffer, buffer.size)
                try {
                    boundServer.receive(request)
                } catch (_: SocketTimeoutException) {
                    continue
                }
                if (!owns(worker)) break
                val message = String(request.data, request.offset, request.length, Charsets.UTF_8)
                if (message != Protocol.DISCOVERY_REQUEST) continue
                val response = JSONObject()
                    .put("service", "hwtstudio")
                    .put("protocol", Protocol.VERSION)
                    .put("device_id", pairing.deviceId)
                    .put("name", Build.MODEL)
                    .put("http_port", Protocol.HTTP_PORT)
                    .put("app_version", BuildConfig.VERSION_NAME)
                    .put("features", JSONArray(Protocol.ADVERTISED_FEATURES))
                    .toString().toByteArray(Charsets.UTF_8)
                boundServer.send(DatagramPacket(response, response.size, request.address, request.port))
            }
        } catch (exc: Exception) {
            if (!bound) startupFailure.compareAndSet(null, exc)
            if (owns(worker)) ReceiverState.update { it.copy(error = "UDP 自动发现服务启动失败，可在电脑手动输入手机 IP") }
        } finally {
            ready.countDown()
            server?.close()
            synchronized(lock) {
                if (socket === server) socket = null
                if (workerThread === Thread.currentThread()) workerThread = null
                if (executor === worker) {
                    executor = null
                    running = false
                }
            }
            worker.shutdown()
        }
    }

    fun stop() = stopCurrent(null)

    private fun stopCurrent(expected: ExecutorService?) {
        val currentSocket: DatagramSocket?
        val currentExecutor: ExecutorService?
        val currentWorker: Thread?
        synchronized(lock) {
            if (expected != null && executor !== expected) return
            running = false
            currentSocket = socket
            socket = null
            currentExecutor = executor
            currentWorker = workerThread
            executor = null
        }
        currentSocket?.close()
        currentExecutor?.shutdownNow()
        if (currentExecutor != null && Thread.currentThread() !== currentWorker) {
            runCatching { currentExecutor.awaitTermination(2, TimeUnit.SECONDS) }
        }
    }

    private fun owns(worker: ExecutorService): Boolean = synchronized(lock) {
        running && executor === worker
    }

    companion object {
        private const val STARTUP_TIMEOUT_MS = 2_000L

        fun localAddresses(): List<String> = runCatching {
            NetworkInterface.getNetworkInterfaces().toList()
                .filter { it.isUp && !it.isLoopback }
                .flatMap { it.inetAddresses.toList() }
                .filterIsInstance<Inet4Address>()
                .filter { !it.isLoopbackAddress }
                .map { it.hostAddress ?: "" }
                .filter { it.isNotBlank() && !it.startsWith("169.254.") }
                .distinct()
                .sortedWith(compareBy<String> { !it.startsWith("192.168.") && !it.startsWith("10.") }.thenBy { it })
        }.getOrDefault(emptyList())
    }
}
