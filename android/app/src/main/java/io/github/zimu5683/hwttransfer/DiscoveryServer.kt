package io.github.zimu5683.hwttransfer

import android.os.Build
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.SocketTimeoutException
import java.util.concurrent.Executors

class DiscoveryServer(private val pairing: PairingManager) {
    @Volatile private var running = false
    private var socket: DatagramSocket? = null
    private val executor = Executors.newSingleThreadExecutor()

    fun start() {
        if (running) return
        running = true
        executor.execute {
            try {
                val server = DatagramSocket(null).apply {
                    reuseAddress = true
                    broadcast = true
                    soTimeout = 1000
                    bind(InetSocketAddress(Protocol.DISCOVERY_PORT))
                }
                socket = server
                val buffer = ByteArray(2048)
                while (running) {
                    val request = DatagramPacket(buffer, buffer.size)
                    try {
                        server.receive(request)
                    } catch (_: SocketTimeoutException) {
                        continue
                    }
                    val message = String(request.data, request.offset, request.length, Charsets.UTF_8)
                    if (message != Protocol.DISCOVERY_REQUEST) continue
                    val response = JSONObject()
                        .put("service", "hwtstudio")
                        .put("protocol", Protocol.VERSION)
                        .put("device_id", pairing.deviceId)
                        .put("name", Build.MODEL)
                        .put("http_port", Protocol.HTTP_PORT)
                        .put("app_version", BuildConfig.VERSION_NAME)
                        .toString().toByteArray(Charsets.UTF_8)
                    server.send(DatagramPacket(response, response.size, request.address, request.port))
                }
            } catch (_: Exception) {
                if (running) ReceiverState.update { it.copy(error = "UDP 自动发现服务启动失败，可在电脑手动输入手机 IP") }
            } finally {
                socket?.close()
                socket = null
            }
        }
    }

    fun stop() {
        running = false
        socket?.close()
        executor.shutdownNow()
    }

    companion object {
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
