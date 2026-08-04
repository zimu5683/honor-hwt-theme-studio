package io.github.zimu5683.hwttransfer

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import org.junit.runner.RunWith
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketException
import java.util.concurrent.TimeUnit

@RunWith(AndroidJUnit4::class)
class DiscoveryServerTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val loopback = InetAddress.getByName("127.0.0.1")

    @Test
    fun responseUsesRequesterEndpointAndAdvertisesAllFeatures() {
        val server = DiscoveryServer(PairingManager(context), bindPort = 0)
        val client = DatagramSocket(null)
        try {
            client.bind(InetSocketAddress(loopback, 0))
            client.soTimeout = 2_000
            server.start()
            val port = awaitPort(server)
            val request = Protocol.DISCOVERY_REQUEST.toByteArray(Charsets.UTF_8)
            client.send(DatagramPacket(request, request.size, loopback, port))

            val response = DatagramPacket(ByteArray(4 * 1024), 4 * 1024)
            client.receive(response)

            assertEquals(server.localPort, response.port)
            assertEquals(loopback, response.address)
            val payload = JSONObject(String(response.data, response.offset, response.length, Charsets.UTF_8))
            assertEquals("hwtstudio", payload.getString("service"))
            assertEquals(Protocol.VERSION, payload.getInt("protocol"))
            assertEquals(Protocol.HTTP_PORT, payload.getInt("http_port"))
            val features = payload.getJSONArray("features")
            assertEquals(Protocol.ADVERTISED_FEATURES.size, features.length())
            assertEquals(
                Protocol.ADVERTISED_FEATURES,
                (0 until features.length()).map { features.getString(it) },
            )
        } finally {
            client.close()
            server.stop()
        }
    }

    @Test
    fun stopReleasesSocketAndAllowsImmediateRestart() {
        val server = DiscoveryServer(PairingManager(context), bindPort = 0)
        var replacement: DiscoveryServer? = null
        try {
            server.start()
            val firstPort = awaitPort(server)
            assertTrue(server.isRunning)

            server.stop()
            assertFalse(server.isRunning)
            assertEquals(-1, server.localPort)

            server.start()
            assertTrue(awaitPort(server) > 0)
            server.stop()

            replacement = DiscoveryServer(PairingManager(context), bindPort = firstPort)
            replacement.start()
            assertEquals(firstPort, awaitPort(replacement))
        } finally {
            server.stop()
            replacement?.stop()
        }
    }

    @Test
    fun startupFailureIsReportedAndWorkerIsReleased() {
        val server = DiscoveryServer(
            PairingManager(context),
            bindPort = 0,
            socketFactory = { throw SocketException("simulated bind failure") },
        )
        try {
            val error = runCatching { server.start() }.exceptionOrNull()

            assertTrue(error is IOException)
            assertTrue(error?.cause is SocketException)
            assertFalse(server.isRunning)
            assertEquals(-1, server.localPort)
        } finally {
            server.stop()
        }
    }

    private fun awaitPort(server: DiscoveryServer): Int {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(3)
        while (System.nanoTime() < deadline) {
            val port = server.localPort
            if (port > 0) return port
            Thread.sleep(10)
        }
        fail("Discovery server did not bind a UDP port")
        return -1
    }
}
