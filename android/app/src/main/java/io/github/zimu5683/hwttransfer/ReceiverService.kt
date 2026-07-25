package io.github.zimu5683.hwttransfer

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import fi.iki.elonen.NanoHTTPD
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicLong

class ReceiverService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var pairing: PairingManager
    private lateinit var storage: ThemeStorage
    private var httpServer: ReceiverServer? = null
    private var discoveryServer: DiscoveryServer? = null
    private var timeoutJob: Job? = null
    private val lastActivity = AtomicLong(0L)

    override fun onCreate() {
        super.onCreate()
        createChannels()
        pairing = PairingManager(this)
        storage = ThemeStorage(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action ?: ACTION_START) {
            ACTION_STOP -> stopSelf()
            ACTION_REGENERATE_CODE -> {
                pairing.regenerateCode()
                publishState()
            }
            else -> startReceiver()
        }
        return START_NOT_STICKY
    }

    private fun startReceiver() {
        if (httpServer != null) return
        if (!storage.isAvailable()) {
            ReceiverState.update { it.copy(error = "请先授权 Honor/Themes 目录") }
            stopSelf()
            return
        }
        startForeground(RECEIVER_NOTIFICATION_ID, receiverNotification())
        lastActivity.set(System.currentTimeMillis())
        try {
            httpServer = ReceiverServer(this, pairing, storage, ::touch, ::onTransfer).also {
                it.start(NanoHTTPD.SOCKET_READ_TIMEOUT, false)
            }
            discoveryServer = DiscoveryServer(pairing).also { it.start() }
        } catch (exc: Exception) {
            ReceiverState.update { it.copy(error = "接收服务启动失败：${exc.message}") }
            stopSelf()
            return
        }
        publishState()
        timeoutJob = scope.launch {
            while (isActive) {
                delay(30_000L)
                if (System.currentTimeMillis() - lastActivity.get() >= Protocol.IDLE_TIMEOUT_MS) {
                    stopSelf()
                    break
                }
            }
        }
    }

    private fun touch() {
        lastActivity.set(System.currentTimeMillis())
    }

    private fun publishState() {
        ReceiverState.update {
            it.copy(
                running = httpServer != null,
                pairCode = pairing.code,
                codeExpiresAt = pairing.codeExpiresAt,
                addresses = DiscoveryServer.localAddresses(),
                destination = storage.destinationLabel(),
                clients = pairing.clients(),
                error = "",
            )
        }
    }

    private fun onTransfer(result: InstallResult) {
        touch()
        ReceiverState.update { it.copy(lastTransfer = "${result.storedName}（${formatSize(result.size)}）", error = "") }
        if (ReceiverState.activityVisible) {
            Handler(Looper.getMainLooper()).post { openThemeManager() }
        } else {
            val manager = getSystemService(NotificationManager::class.java)
            manager.notify(SUCCESS_NOTIFICATION_ID, successNotification(result))
        }
    }

    private fun openThemeManager() {
        val launch = packageManager.getLaunchIntentForPackage(THEME_PACKAGE)
        if (launch != null) {
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            runCatching { startActivity(launch) }
        }
    }

    private fun receiverNotification() = NotificationCompat.Builder(this, RECEIVER_CHANNEL)
        .setSmallIcon(android.R.drawable.stat_sys_upload)
        .setContentTitle("荣耀主题传输助手正在接收")
        .setContentText("电脑可自动发现本机；30 分钟无活动后停止")
        .setOngoing(true)
        .setContentIntent(mainPendingIntent())
        .addAction(0, "停止", PendingIntent.getService(
            this, 2, Intent(this, ReceiverService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        ))
        .build()

    private fun successNotification(result: InstallResult) = NotificationCompat.Builder(this, SUCCESS_CHANNEL)
        .setSmallIcon(android.R.drawable.stat_sys_download_done)
        .setContentTitle("主题已保存")
        .setContentText(result.storedName)
        .setAutoCancel(true)
        .setContentIntent(themePendingIntent())
        .addAction(0, "打开荣耀主题", themePendingIntent())
        .build()

    private fun mainPendingIntent(): PendingIntent = PendingIntent.getActivity(
        this, 3, Intent(this, MainActivity::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    private fun themePendingIntent(): PendingIntent {
        val intent = packageManager.getLaunchIntentForPackage(THEME_PACKAGE) ?: Intent(this, MainActivity::class.java)
        return PendingIntent.getActivity(this, 4, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun createChannels() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(RECEIVER_CHANNEL, getString(R.string.receiver_channel), NotificationManager.IMPORTANCE_LOW))
        manager.createNotificationChannel(NotificationChannel(SUCCESS_CHANNEL, getString(R.string.success_channel), NotificationManager.IMPORTANCE_DEFAULT))
    }

    override fun onDestroy() {
        timeoutJob?.cancel()
        httpServer?.stop()
        discoveryServer?.stop()
        httpServer = null
        discoveryServer = null
        ReceiverState.update { it.copy(running = false, pairCode = "------", codeExpiresAt = 0L, addresses = emptyList()) }
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        const val ACTION_START = "io.github.zimu5683.hwttransfer.START"
        const val ACTION_STOP = "io.github.zimu5683.hwttransfer.STOP"
        const val ACTION_REGENERATE_CODE = "io.github.zimu5683.hwttransfer.REGENERATE"
        private const val THEME_PACKAGE = "com.hihonor.android.thememanager"
        private const val RECEIVER_CHANNEL = "hwt_receiver"
        private const val SUCCESS_CHANNEL = "hwt_success"
        private const val RECEIVER_NOTIFICATION_ID = 1001
        private const val SUCCESS_NOTIFICATION_ID = 1002

        fun formatSize(size: Long): String = if (size >= 1024L * 1024L) {
            "%.2f MiB".format(size.toDouble() / 1024.0 / 1024.0)
        } else {
            "%.1f KiB".format(size.toDouble() / 1024.0)
        }
    }
}
