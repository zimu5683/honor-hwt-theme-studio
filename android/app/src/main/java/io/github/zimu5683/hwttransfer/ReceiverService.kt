package io.github.zimu5683.hwttransfer

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Environment
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import java.io.File
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
    @Volatile private var httpServer: ReceiverServer? = null
    private var discoveryServer: DiscoveryServer? = null
    private var timeoutJob: Job? = null
    private val lastActivity = AtomicLong(0L)
    private val receiverGeneration = AtomicLong(0L)
    private val lifecycleLock = Any()

    override fun onCreate() {
        super.onCreate()
        createChannels()
        pairing = PairingManager(this)
        storage = ThemeStorage(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action ?: ACTION_START) {
            ACTION_STOP -> stopReceiver()
            ACTION_REGENERATE_CODE -> regenerateCode()
            else -> startReceiver()
        }
        return START_NOT_STICKY
    }

    private fun regenerateCode() {
        synchronized(lifecycleLock) {
            if (httpServer == null) {
                stopSelf()
                return
            }
            pairing.regenerateCode()
            publishState()
        }
    }

    private fun startReceiver() {
        val generation = synchronized(lifecycleLock) {
            if (httpServer != null) return
            receiverGeneration.incrementAndGet()
        }
        if (!storage.isAvailable()) {
            ReceiverState.update { it.copy(error = "请先授权 Honor/Themes 目录") }
            stopReceiver()
            return
        }
        try {
            startForeground(RECEIVER_NOTIFICATION_ID, receiverNotification())
        } catch (_: Exception) {
            ReceiverState.update { it.copy(error = "接收服务启动失败，请检查通知权限和系统设置") }
            stopReceiver()
            return
        }
        lastActivity.set(System.currentTimeMillis())
        try {
            httpServer = ReceiverServer(this, pairing, storage, ::touch, ::onTransfer).also {
                it.start(NanoHTTPD.SOCKET_READ_TIMEOUT, false)
            }
            discoveryServer = DiscoveryServer(pairing).also { it.start() }
        } catch (exc: Exception) {
            android.util.Log.e("ReceiverService", "Receiver service start failed", exc)
            ReceiverState.update { it.copy(error = "接收服务启动失败，请检查通知权限、网络端口和目录授权") }
            stopReceiver()
            return
        }
        publishState()
        timeoutJob = scope.launch {
            while (isActive) {
                delay(30_000L)
                if (receiverGeneration.get() != generation) break
                val server = httpServer
                if (server?.hasActiveRequests() == true) {
                    lastActivity.set(System.currentTimeMillis())
                    continue
                }
                if (Protocol.shouldStopForIdle(System.currentTimeMillis(), lastActivity.get(), 0)) {
                    val stoppingGeneration = generation + 1L
                    if (receiverGeneration.compareAndSet(generation, stoppingGeneration)) {
                        clearReceiverResources(stoppingGeneration)
                        stopSelf()
                    }
                    break
                }
            }
        }
    }

    private fun touch() {
        lastActivity.set(System.currentTimeMillis())
    }

    private fun stopReceiver() {
        receiverGeneration.incrementAndGet()
        clearReceiverResources()
        stopSelf()
    }

    private fun clearReceiverResources(expectedGeneration: Long? = null) {
        synchronized(lifecycleLock) {
            if (expectedGeneration != null && receiverGeneration.get() != expectedGeneration) return
            timeoutJob?.cancel()
            timeoutJob = null
            val http = httpServer
            val discovery = discoveryServer
            httpServer = null
            discoveryServer = null
            try {
                http?.shutdownTransfers()
            } catch (exc: Exception) {
                android.util.Log.e("ReceiverService", "Transfer cleanup failed", exc)
            }
            try {
                http?.stop()
            } catch (exc: Exception) {
                android.util.Log.e("ReceiverService", "HTTP receiver stop failed", exc)
            }
            try {
                discovery?.stop()
            } catch (exc: Exception) {
                android.util.Log.e("ReceiverService", "Discovery receiver stop failed", exc)
            }
            try {
                stopForeground(STOP_FOREGROUND_REMOVE)
            } catch (exc: Exception) {
                android.util.Log.e("ReceiverService", "Foreground notification cleanup failed", exc)
            }
            ReceiverState.update { it.copy(running = false, pairCode = "------", codeExpiresAt = 0L, addresses = emptyList()) }
        }
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
        verifyInBackground(result)
    }

    /**
     * Decompress and validate the installed theme in the background. Receipt
     * has already been acknowledged to the computer, so a corrupt archive is
     * reported here instead of blocking the transfer.
     */
    private fun verifyInBackground(result: InstallResult) {
        val tree = storage.treeUri()
        scope.launch {
            val verificationError: String? = try {
                if (tree != null) {
                    storage.verifySafInstall(tree, result.storedName, result.sha256)
                } else {
                    storage.verifyDirectInstall(
                        File(Environment.getExternalStorageDirectory(), "Honor/Themes"),
                        result.storedName,
                        result.sha256,
                    )
                }
                null
            } catch (exc: Exception) {
                (exc as? TransferException)?.message
                    ?: exc.message
                    ?: "主题文件校验失败"
            }
            if (verificationError != null) {
                android.util.Log.e("ReceiverService", "Background theme verification failed", RuntimeException(verificationError))
                val notification = NotificationCompat.Builder(this@ReceiverService, SUCCESS_CHANNEL)
                    .setSmallIcon(android.R.drawable.stat_sys_warning)
                    .setContentTitle("主题校验未通过")
                    .setContentText("${result.storedName}：$verificationError")
                    .setAutoCancel(true)
                    .setContentIntent(mainPendingIntent())
                    .build()
                runCatching {
                    getSystemService(NotificationManager::class.java)
                        .notify(VERIFY_FAILURE_NOTIFICATION_ID, notification)
                }
                ReceiverState.update {
                    it.copy(error = "主题校验未通过：${result.storedName}（$verificationError）")
                }
            }
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
        clearReceiverResources()
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
        private const val VERIFY_FAILURE_NOTIFICATION_ID = 1003

        fun formatSize(size: Long): String = if (size >= 1024L * 1024L) {
            "%.2f MiB".format(size.toDouble() / 1024.0 / 1024.0)
        } else {
            "%.1f KiB".format(size.toDouble() / 1024.0)
        }
    }
}
