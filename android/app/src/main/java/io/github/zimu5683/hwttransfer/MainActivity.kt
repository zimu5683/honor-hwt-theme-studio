package io.github.zimu5683.hwttransfer

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import com.google.zxing.integration.android.IntentIntegrator
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.DateFormat
import java.util.Date

private enum class StudioButtonKind { Primary, Secondary, Tertiary, Ghost, Danger }

class MainActivity : ComponentActivity() {
    private lateinit var storage: ThemeStorage
    private lateinit var pairing: PairingManager

    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            startReceiverNow()
        } else {
            toast("未授予通知权限，无法启动后台接收服务")
        }
    }

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            launchQrScan()
        } else {
            toast("未授予相机权限，无法扫码连接电脑")
        }
    }

    private val qrScanner = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val content = IntentIntegrator.parseActivityResult(result.resultCode, result.data)?.contents
        if (content.isNullOrBlank()) {
            toast("未识别到二维码")
        } else {
            registerWithComputer(content)
        }
    }

    private fun launchQrScan() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            cameraPermission.launch(Manifest.permission.CAMERA)
            return
        }
        runCatching {
            qrScanner.launch(IntentIntegrator(this).apply {
                setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
                setPrompt("将电脑屏幕上的二维码放入框内")
                setBeepEnabled(false)
                setOrientationLocked(false)
            }.createScanIntent())
        }.onFailure {
            toast("无法打开相机，请检查相机权限")
        }
    }

    /** 解析电脑二维码（hwtstudio://IP:端口?s=会话）并把本机地址与配对码注册到电脑。 */
    private fun registerWithComputer(url: String) {
        val parsed = parseConnectUrl(url) ?: run {
            toast("这不是“大雪主题编辑器”的连接二维码")
            return
        }
        val (host, port, session) = parsed
        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    val body = JSONObject()
                        .put("s", session)
                        .put("name", Build.MODEL)
                        .put("device_id", pairing.deviceId)
                        .put("http_port", Protocol.HTTP_PORT)
                        .put("pair_code", pairing.code)
                        .toString()
                    val connection = URL("http://$host:$port/api/v1/register").openConnection() as HttpURLConnection
                    try {
                        connection.requestMethod = "POST"
                        connection.connectTimeout = 5000
                        connection.readTimeout = 8000
                        connection.doOutput = true
                        connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                        OutputStreamWriter(connection.outputStream, Charsets.UTF_8).use { it.write(body) }
                        connection.responseCode
                    } finally {
                        connection.disconnect()
                    }
                }
                if (result == 200) {
                    toast("已把手机信息发送给电脑，请在电脑上点击“发送”")
                } else {
                    toast("电脑拒绝了扫码连接（错误 $result），请刷新电脑二维码后重试")
                }
            } catch (exc: Exception) {
                toast("无法连接电脑：${exc.message ?: "网络异常"}")
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        storage = ThemeStorage(this)
        pairing = PairingManager(this)
        refreshState()
        checkUpdates()
        setContent {
            StudioSoftTheme {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    ReceiverScreen()
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        ReceiverState.activityVisible = true
        refreshState()
    }

    override fun onPause() {
        ReceiverState.activityVisible = false
        super.onPause()
    }

    private fun refreshState() {
        ReceiverState.update { it.copy(destination = storage.destinationLabel(), clients = pairing.clients()) }
    }

    private fun requestStartReceiver() {
        if (!storage.isAvailable()) {
            toast("请先选择 Honor/Themes 目录")
            return
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            startReceiverNow()
        }
    }

    private fun startReceiverNow() {
        ContextCompat.startForegroundService(
            this,
            Intent(this, ReceiverService::class.java).setAction(ReceiverService.ACTION_START),
        )
    }

    private fun stopReceiver() {
        startService(Intent(this, ReceiverService::class.java).setAction(ReceiverService.ACTION_STOP))
    }

    private fun regenerateCode() {
        startService(Intent(this, ReceiverService::class.java).setAction(ReceiverService.ACTION_REGENERATE_CODE))
    }

    private fun grantAllFiles() {
        val intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:$packageName"))
        runCatching { startActivity(intent) }.onFailure {
            startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
        }
    }

    private fun importTheme(uri: Uri) {
        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) { storage.importUri(uri) }
                ReceiverState.update { it.copy(lastTransfer = "${result.storedName}（${ReceiverService.formatSize(result.size)}）") }
                toast("已保存到 ${result.destination}")
            } catch (exc: Exception) {
                toast(if (exc is TransferException) exc.message else "导入失败")
            }
        }
    }

    private fun openThemeManager() {
        val intent = packageManager.getLaunchIntentForPackage("com.hihonor.android.thememanager")
        if (intent == null) toast("未找到荣耀主题应用") else startActivity(intent)
    }

    private fun checkUpdates() {
        ReceiverState.update { it.copy(updateBusy = true, updateMessage = "正在检查更新…") }
        lifecycleScope.launch {
            val result = runCatching { Updater.checkForUpdate() }
            val message = when {
                result.isFailure -> "检查更新失败：${(result.exceptionOrNull() as? UpdateException)?.message ?: "网络异常"}"
                result.getOrNull() == null -> "已是最新版本"
                else -> "发现新版本 ${result.getOrNull()!!.version}"
            }
            ReceiverState.update { it.copy(updateBusy = false, updateMessage = message, updateAvailable = result.getOrNull()) }
        }
    }

    private fun downloadAndInstall(update: AndroidUpdate) {
        ReceiverState.update { it.copy(updateBusy = true, updateMessage = "正在下载更新…") }
        lifecycleScope.launch {
            try {
                val apk = Updater.downloadApk(this@MainActivity, update) { _, _ -> }
                ReceiverState.update { it.copy(updateBusy = false, updateMessage = "下载完成，准备安装…") }
                if (!Updater.installApk(this@MainActivity, apk)) {
                    ReceiverState.update { it.copy(updateMessage = "请在系统弹窗中允许安装未知来源，然后重新点击更新") }
                }
            } catch (exc: Exception) {
                ReceiverState.update {
                    it.copy(updateBusy = false, updateMessage = "更新失败：${(exc as? UpdateException)?.message ?: exc.message ?: "未知错误"}")
                }
            }
        }
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    @Composable
    private fun ReceiverScreen() {
        val state by ReceiverState.state.collectAsState()
        val directoryPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
            if (uri != null) {
                val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                val alreadyPersisted = contentResolver.persistedUriPermissions.any {
                    it.uri == uri && it.isReadPermission && it.isWritePermission
                }
                val permissionGranted = alreadyPersisted || runCatching {
                    contentResolver.takePersistableUriPermission(uri, flags)
                }.isSuccess
                if (!permissionGranted) {
                    toast("系统未授予长期目录权限，请重新选择 Honor/Themes")
                } else {
                    lifecycleScope.launch {
                        try {
                            withContext(Dispatchers.IO) { storage.validateAndPersistTree(uri) }
                            refreshState()
                            toast("Honor/Themes 目录授权成功")
                        } catch (exc: CancellationException) {
                            if (!alreadyPersisted) storage.discardSaf(uri)
                            throw exc
                        } catch (exc: Exception) {
                            if (!alreadyPersisted) {
                                storage.discardSaf(uri)
                            }
                            toast(if (exc is TransferException) exc.message else "目录授权失败")
                        }
                    }
                }
            }
        }
        val filePicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) importTheme(uri)
        }

        BoxWithConstraints(Modifier.fillMaxSize()) {
            val compact = maxWidth < 672.dp
            Column(
                modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(
                    horizontal = if (compact) 16.dp else 24.dp,
                    vertical = 24.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    color = StudioSemanticColors.lavender,
                    shape = MaterialTheme.shapes.extraLarge,
                ) {
                    Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("荣耀主题传输助手", style = MaterialTheme.typography.headlineLarge, color = StudioSemanticColors.purpleDeep)
                        Text(
                            "无需 Termux。授权一次主题目录后，电脑上的大雪主题编辑器即可直接发送 HWT。",
                            style = MaterialTheme.typography.bodyLarge,
                            color = StudioSemanticColors.purpleDeep.copy(alpha = 0.82f),
                        )
                    }
                }

                val directorySection: @Composable () -> Unit = {
                    StudioSection("主题目录", StudioSemanticColors.sky) {
                        Text(state.destination, style = MaterialTheme.typography.bodyLarge)
                        StudioButtonRow(compact) {
                            StudioButton(
                                text = "选择 Honor/Themes",
                                onClick = { directoryPicker.launch(null) },
                                kind = StudioButtonKind.Primary,
                                modifier = Modifier.weightOrFill(compact),
                            )
                            StudioButton(
                                text = "全盘权限备用",
                                onClick = ::grantAllFiles,
                                kind = StudioButtonKind.Tertiary,
                                modifier = Modifier.weightOrFill(compact),
                            )
                        }
                        Text(
                            "优先使用目录授权；只有 MagicOS 文件选择器无法授权时才使用全盘权限。",
                            style = MaterialTheme.typography.bodySmall,
                            color = StudioSemanticColors.muted,
                        )
                    }
                }

                val receiverSection: @Composable () -> Unit = {
                    StudioSection("电脑接收", StudioSemanticColors.mint) {
                        val statusColor = if (state.running) StudioSemanticColors.success else StudioSemanticColors.muted
                        StudioStatusLine(
                            text = if (state.running) "状态：正在接收" else "状态：未启动",
                            color = statusColor,
                        )
                        if (state.running) {
                            Text("配对码：${state.pairCode}", style = MaterialTheme.typography.displayLarge, color = MaterialTheme.colorScheme.primary)
                            if (state.codeExpiresAt > 0) {
                                Text(
                                    "有效期至 ${DateFormat.getTimeInstance(DateFormat.MEDIUM).format(Date(state.codeExpiresAt))}",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = StudioSemanticColors.muted,
                                )
                            }
                            Text("手机地址：${state.addresses.joinToString().ifBlank { "等待网络地址" }}")
                            Text("端口：${Protocol.HTTP_PORT}；30 分钟无活动自动停止", style = MaterialTheme.typography.bodySmall)
                            val firstAddress = state.addresses.firstOrNull()
                            val qrContent = firstAddress?.let { connectUrl(it) }
                            val qrBitmap = remember(qrContent) { qrContent?.let(::qrBitmap) }
                            if (qrContent != null && qrBitmap != null) {
                                Surface(
                                    shape = RoundedCornerShape(12.dp),
                                    color = Color.White,
                                    border = BorderStroke(1.dp, StudioSemanticColors.hairline),
                                ) {
                                    Image(
                                        bitmap = qrBitmap.asImageBitmap(),
                                        contentDescription = "电脑扫码连接二维码",
                                        modifier = Modifier.size(180.dp).padding(8.dp),
                                    )
                                }
                                Text(
                                    "电脑上点击“扫码”扫描此二维码即可连接，无需手动输入地址。",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = StudioSemanticColors.muted,
                                )
                            }
                        }
                        StudioButtonRow(compact) {
                            StudioButton(
                                text = if (state.running) "停止接收" else "开始接收",
                                onClick = { if (state.running) stopReceiver() else requestStartReceiver() },
                                kind = if (state.running) StudioButtonKind.Danger else StudioButtonKind.Primary,
                                modifier = Modifier.weightOrFill(compact),
                            )
                            if (state.running) {
                                StudioButton(
                                    text = "刷新配对码",
                                    onClick = ::regenerateCode,
                                    kind = StudioButtonKind.Tertiary,
                                    modifier = Modifier.weightOrFill(compact),
                                )
                            }
                        }
                        StudioButton(
                            text = "扫电脑码连接",
                            onClick = ::launchQrScan,
                            kind = StudioButtonKind.Secondary,
                            modifier = Modifier.weightOrFill(compact),
                        )
                        Text(
                            "电脑上打开配对窗口后点击“手机扫码”标签，用本机扫描电脑屏幕上的二维码，即可免输地址配对。",
                            style = MaterialTheme.typography.bodySmall,
                            color = StudioSemanticColors.muted,
                        )
                    }
                }

                val importSection: @Composable () -> Unit = {
                    StudioSection("本地导入", StudioSemanticColors.peach) {
                        Text("也可以先通过荣耀分享、微信或数据线把 HWT 放到手机，再从这里导入。")
                        StudioButtonRow(compact) {
                            StudioButton(
                                text = "选择 HWT 文件",
                                onClick = { filePicker.launch(arrayOf("application/octet-stream", "application/zip", "*/*")) },
                                kind = StudioButtonKind.Primary,
                                modifier = Modifier.weightOrFill(compact),
                            )
                            StudioButton(
                                text = "打开荣耀主题",
                                onClick = ::openThemeManager,
                                kind = StudioButtonKind.Ghost,
                                modifier = Modifier.weightOrFill(compact),
                            )
                        }
                        Text("最近一次：${state.lastTransfer}", style = MaterialTheme.typography.bodySmall, color = StudioSemanticColors.muted)
                    }
                }

                val clientsSection: @Composable () -> Unit = {
                    StudioSection("已配对电脑", StudioSemanticColors.lavender) {
                        if (state.clients.isEmpty()) {
                            Text("暂无", color = StudioSemanticColors.muted)
                        } else {
                            state.clients.forEachIndexed { index, client ->
                                if (index > 0) HorizontalDivider(color = StudioSemanticColors.hairline)
                                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                        Text(client.name, style = MaterialTheme.typography.bodyLarge)
                                        Text(
                                            DateFormat.getDateTimeInstance().format(Date(client.pairedAt)),
                                            style = MaterialTheme.typography.bodySmall,
                                            color = StudioSemanticColors.muted,
                                        )
                                    }
                                    StudioButton(
                                        text = "撤销",
                                        onClick = {
                                            pairing.revoke(client.tokenHash)
                                            refreshState()
                                        },
                                        kind = StudioButtonKind.Ghost,
                                    )
                                }
                            }
                        }
                    }
                }

                val updateSection: @Composable () -> Unit = {
                    StudioSection("应用更新", StudioSemanticColors.mint) {
                        if (state.updateMessage.isNotBlank()) {
                            Text(state.updateMessage, style = MaterialTheme.typography.bodyLarge)
                        }
                        val update = state.updateAvailable
                        StudioButtonRow(compact) {
                            StudioButton(
                                text = if (update != null) "下载并安装 v${update.version}" else "检查更新",
                                onClick = { if (update != null) downloadAndInstall(update) else checkUpdates() },
                                kind = StudioButtonKind.Primary,
                                enabled = !state.updateBusy,
                                modifier = Modifier.weightOrFill(compact),
                            )
                        }
                    }
                }

                if (compact) {
                    directorySection()
                    receiverSection()
                    importSection()
                    clientsSection()
                    updateSection()
                } else {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                            directorySection()
                            importSection()
                            updateSection()
                        }
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                            receiverSection()
                            clientsSection()
                        }
                    }
                }

                if (state.error.isNotBlank()) {
                    StudioStatusLine(text = "错误：${state.error}", color = StudioSemanticColors.error, background = StudioSemanticColors.rose)
                }
                Spacer(Modifier.height(8.dp))
                Text("协议 v${Protocol.VERSION} · APK ${BuildConfig.VERSION_NAME}", style = MaterialTheme.typography.bodySmall, color = StudioSemanticColors.subtle)            }
        }
    }

    @Composable
    private fun StudioSection(title: String, accent: Color, content: @Composable () -> Unit) {
        Surface(
            modifier = Modifier.fillMaxWidth().border(BorderStroke(1.dp, StudioSemanticColors.hairline), MaterialTheme.shapes.large),
            color = MaterialTheme.colorScheme.surface,
            shape = MaterialTheme.shapes.large,
            tonalElevation = 0.dp,
            shadowElevation = 0.dp,
        ) {
            Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Box(
                        Modifier.width(4.dp).height(24.dp).background(accent, RoundedCornerShape(2.dp)),
                    )
                    Text(title, style = MaterialTheme.typography.titleLarge)
                }
                content()
            }
        }
    }

    @Composable
    private fun StudioButton(
        text: String,
        onClick: () -> Unit,
        kind: StudioButtonKind,
        modifier: Modifier = Modifier,
        enabled: Boolean = true,
    ) {
        val colors = when (kind) {
            StudioButtonKind.Primary -> ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
                disabledContainerColor = StudioSemanticColors.surface2,
                disabledContentColor = StudioSemanticColors.subtle,
            )
            StudioButtonKind.Secondary -> ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.secondary,
                contentColor = MaterialTheme.colorScheme.onSecondary,
            )
            StudioButtonKind.Danger -> ButtonDefaults.buttonColors(
                containerColor = StudioSemanticColors.error,
                contentColor = Color.White,
            )
            StudioButtonKind.Tertiary -> ButtonDefaults.outlinedButtonColors(
                containerColor = Color.Transparent,
                contentColor = MaterialTheme.colorScheme.primary,
            )
            StudioButtonKind.Ghost -> ButtonDefaults.textButtonColors(
                contentColor = MaterialTheme.colorScheme.primary,
            )
        }
        val shape = MaterialTheme.shapes.small
        when (kind) {
            StudioButtonKind.Tertiary -> OutlinedButton(
                onClick = onClick,
                enabled = enabled,
                modifier = modifier.heightIn(min = 48.dp),
                shape = shape,
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.primary),
                colors = colors,
            ) { Text(text, style = MaterialTheme.typography.labelLarge) }
            StudioButtonKind.Ghost -> TextButton(
                onClick = onClick,
                enabled = enabled,
                modifier = modifier.heightIn(min = 48.dp),
                shape = shape,
                colors = colors,
            ) { Text(text, style = MaterialTheme.typography.labelLarge) }
            else -> Button(
                onClick = onClick,
                enabled = enabled,
                modifier = modifier.heightIn(min = 48.dp),
                shape = shape,
                elevation = ButtonDefaults.buttonElevation(0.dp, 0.dp, 0.dp, 0.dp, 0.dp),
                colors = colors,
            ) { Text(text, style = MaterialTheme.typography.labelLarge) }
        }
    }

    @Composable
    private fun StudioButtonRow(compact: Boolean, content: @Composable () -> Unit) {
        if (compact) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                // A vertical row keeps every touch target at least 48dp on phones.
                content()
            }
        } else {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) { content() }
        }
    }

    @Composable
    private fun StudioStatusLine(text: String, color: Color, background: Color = StudioSemanticColors.surfaceMuted) {
        Surface(
            modifier = Modifier.fillMaxWidth().border(BorderStroke(1.dp, StudioSemanticColors.hairline), MaterialTheme.shapes.small),
            color = background,
            shape = MaterialTheme.shapes.small,
        ) {
            Text(text, modifier = Modifier.padding(12.dp), color = color, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

private fun Modifier.weightOrFill(compact: Boolean): Modifier = if (compact) {
    fillMaxWidth()
} else {
    this
}

/** 解析电脑二维码内容，返回 (主机, 端口, 会话令牌)；格式不符返回 null。 */
internal fun parseConnectUrl(raw: String): Triple<String, Int, String>? {
    val url = raw.trim()
    if (!url.startsWith("hwtstudio://")) return null
    val rest = url.removePrefix("hwtstudio://")
    val queryIndex = rest.indexOf('?')
    val session = if (queryIndex >= 0) rest.substring(queryIndex + 1).removePrefix("s=") else ""
    val hostPort = if (queryIndex >= 0) rest.substring(0, queryIndex) else rest
    var host = hostPort
    var port = Protocol.HTTP_PORT
    if (hostPort.startsWith("[")) {
        val closing = hostPort.indexOf(']')
        if (closing <= 1) return null
        host = hostPort.substring(1, closing)
        val suffix = hostPort.substring(closing + 1)
        if (suffix.isNotEmpty()) {
            if (!suffix.startsWith(":") || suffix.length == 1) return null
            port = suffix.substring(1).toIntOrNull() ?: return null
        }
    } else {
        val colon = hostPort.lastIndexOf(':')
        if (colon >= 0) {
            val candidatePort = hostPort.substring(colon + 1).toIntOrNull()
            if (candidatePort == null) {
                return null
            }
            if (candidatePort in 1..65535) {
                host = hostPort.substring(0, colon)
                port = candidatePort
            } else {
                return null
            }
        }
    }
    if (host.isBlank() || port !in 1..65535 || session.isBlank()) return null
    return Triple(host, port, session)
}
