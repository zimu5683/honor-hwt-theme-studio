package io.github.zimu5683.hwttransfer

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.DateFormat
import java.util.Date

private enum class CarbonButtonKind { Primary, Secondary, Tertiary, Ghost, Danger }

class MainActivity : ComponentActivity() {
    private lateinit var storage: ThemeStorage
    private lateinit var pairing: PairingManager

    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        startReceiverNow()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        storage = ThemeStorage(this)
        pairing = PairingManager(this)
        refreshState()
        setContent {
            CarbonTheme {
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
                toast(exc.message ?: "导入失败")
            }
        }
    }

    private fun openThemeManager() {
        val intent = packageManager.getLaunchIntentForPackage("com.hihonor.android.thememanager")
        if (intent == null) toast("未找到荣耀主题应用") else startActivity(intent)
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_LONG).show()

    @Composable
    private fun ReceiverScreen() {
        val state by ReceiverState.state.collectAsState()
        val directoryPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
            if (uri != null) {
                val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                runCatching { contentResolver.takePersistableUriPermission(uri, flags) }
                lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) { storage.validateAndPersistTree(uri) }
                        refreshState()
                        toast("Honor/Themes 目录授权成功")
                    } catch (exc: Exception) {
                        toast(exc.message ?: "目录授权失败")
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
                Text("荣耀主题传输助手", style = MaterialTheme.typography.headlineLarge)
                Text(
                    "无需 Termux。授权一次主题目录后，电脑上的大雪主题编辑器即可直接发送 HWT。",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                val directorySection: @Composable () -> Unit = {
                    CarbonSection("主题目录") {
                        Text(state.destination, style = MaterialTheme.typography.bodyLarge)
                        CarbonButtonRow(compact) {
                            CarbonButton(
                                text = "选择 Honor/Themes",
                                onClick = { directoryPicker.launch(null) },
                                kind = CarbonButtonKind.Primary,
                                modifier = Modifier.weightOrFill(compact),
                            )
                            CarbonButton(
                                text = "全盘权限备用",
                                onClick = ::grantAllFiles,
                                kind = CarbonButtonKind.Tertiary,
                                modifier = Modifier.weightOrFill(compact),
                            )
                        }
                        Text(
                            "优先使用目录授权；只有 MagicOS 文件选择器无法授权时才使用全盘权限。",
                            style = MaterialTheme.typography.bodySmall,
                            color = CarbonSemanticColors.muted,
                        )
                    }
                }

                val receiverSection: @Composable () -> Unit = {
                    CarbonSection("电脑接收") {
                        val statusColor = if (state.running) CarbonSemanticColors.success else CarbonSemanticColors.muted
                        StatusLine(
                            text = if (state.running) "状态：正在接收" else "状态：未启动",
                            color = statusColor,
                        )
                        if (state.running) {
                            Text("配对码：${state.pairCode}", style = MaterialTheme.typography.displayLarge, color = MaterialTheme.colorScheme.primary)
                            if (state.codeExpiresAt > 0) {
                                Text(
                                    "有效期至 ${DateFormat.getTimeInstance(DateFormat.MEDIUM).format(Date(state.codeExpiresAt))}",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = CarbonSemanticColors.muted,
                                )
                            }
                            Text("手机地址：${state.addresses.joinToString().ifBlank { "等待网络地址" }}")
                            Text("端口：${Protocol.HTTP_PORT}；30 分钟无活动自动停止", style = MaterialTheme.typography.bodySmall)
                        }
                        CarbonButtonRow(compact) {
                            CarbonButton(
                                text = if (state.running) "停止接收" else "开始接收",
                                onClick = { if (state.running) stopReceiver() else requestStartReceiver() },
                                kind = if (state.running) CarbonButtonKind.Danger else CarbonButtonKind.Primary,
                                modifier = Modifier.weightOrFill(compact),
                            )
                            if (state.running) {
                                CarbonButton(
                                    text = "刷新配对码",
                                    onClick = ::regenerateCode,
                                    kind = CarbonButtonKind.Tertiary,
                                    modifier = Modifier.weightOrFill(compact),
                                )
                            }
                        }
                    }
                }

                val importSection: @Composable () -> Unit = {
                    CarbonSection("本地导入") {
                        Text("也可以先通过荣耀分享、微信或数据线把 HWT 放到手机，再从这里导入。")
                        CarbonButtonRow(compact) {
                            CarbonButton(
                                text = "选择 HWT 文件",
                                onClick = { filePicker.launch(arrayOf("application/octet-stream", "application/zip", "*/*")) },
                                kind = CarbonButtonKind.Primary,
                                modifier = Modifier.weightOrFill(compact),
                            )
                            CarbonButton(
                                text = "打开荣耀主题",
                                onClick = ::openThemeManager,
                                kind = CarbonButtonKind.Ghost,
                                modifier = Modifier.weightOrFill(compact),
                            )
                        }
                        Text("最近一次：${state.lastTransfer}", style = MaterialTheme.typography.bodySmall, color = CarbonSemanticColors.muted)
                    }
                }

                val clientsSection: @Composable () -> Unit = {
                    CarbonSection("已配对电脑") {
                        if (state.clients.isEmpty()) {
                            Text("暂无", color = CarbonSemanticColors.muted)
                        } else {
                            state.clients.forEachIndexed { index, client ->
                                if (index > 0) HorizontalDivider(color = CarbonSemanticColors.hairline)
                                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                        Text(client.name, style = MaterialTheme.typography.bodyLarge)
                                        Text(
                                            DateFormat.getDateTimeInstance().format(Date(client.pairedAt)),
                                            style = MaterialTheme.typography.bodySmall,
                                            color = CarbonSemanticColors.muted,
                                        )
                                    }
                                    CarbonButton(
                                        text = "撤销",
                                        onClick = {
                                            pairing.revoke(client.tokenHash)
                                            refreshState()
                                        },
                                        kind = CarbonButtonKind.Ghost,
                                    )
                                }
                            }
                        }
                    }
                }

                if (compact) {
                    directorySection()
                    receiverSection()
                    importSection()
                    clientsSection()
                } else {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                            directorySection()
                            importSection()
                        }
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                            receiverSection()
                            clientsSection()
                        }
                    }
                }

                if (state.error.isNotBlank()) {
                    StatusLine(text = "错误：${state.error}", color = CarbonSemanticColors.error, background = CarbonSemanticColors.error.copy(alpha = 0.08f))
                }
                Spacer(Modifier.height(8.dp))
                Text("协议 v${Protocol.VERSION} · APK ${BuildConfig.VERSION_NAME}", style = MaterialTheme.typography.bodySmall, color = CarbonSemanticColors.subtle)
            }
        }
    }

    @Composable
    private fun CarbonSection(title: String, content: @Composable () -> Unit) {
        Surface(
            modifier = Modifier.fillMaxWidth().border(BorderStroke(1.dp, CarbonSemanticColors.hairline), RoundedCornerShape(0.dp)),
            color = MaterialTheme.colorScheme.surface,
            shape = RoundedCornerShape(0.dp),
            tonalElevation = 0.dp,
            shadowElevation = 0.dp,
        ) {
            Column(Modifier.padding(24.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                Text(title, style = MaterialTheme.typography.titleLarge)
                content()
            }
        }
    }

    @Composable
    private fun CarbonButton(
        text: String,
        onClick: () -> Unit,
        kind: CarbonButtonKind,
        modifier: Modifier = Modifier,
        enabled: Boolean = true,
    ) {
        val colors = when (kind) {
            CarbonButtonKind.Primary -> ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
                disabledContainerColor = CarbonSemanticColors.surface2,
                disabledContentColor = CarbonSemanticColors.subtle,
            )
            CarbonButtonKind.Secondary -> ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.secondary,
                contentColor = MaterialTheme.colorScheme.onSecondary,
            )
            CarbonButtonKind.Danger -> ButtonDefaults.buttonColors(
                containerColor = CarbonSemanticColors.error,
                contentColor = Color.White,
            )
            CarbonButtonKind.Tertiary -> ButtonDefaults.outlinedButtonColors(
                containerColor = Color.Transparent,
                contentColor = MaterialTheme.colorScheme.primary,
            )
            CarbonButtonKind.Ghost -> ButtonDefaults.textButtonColors(
                contentColor = MaterialTheme.colorScheme.primary,
            )
        }
        val shape = RoundedCornerShape(0.dp)
        when (kind) {
            CarbonButtonKind.Tertiary -> OutlinedButton(
                onClick = onClick,
                enabled = enabled,
                modifier = modifier.heightIn(min = 48.dp),
                shape = shape,
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.primary),
                colors = colors,
            ) { Text(text, style = MaterialTheme.typography.labelLarge) }
            CarbonButtonKind.Ghost -> TextButton(
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
    private fun CarbonButtonRow(compact: Boolean, content: @Composable () -> Unit) {
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
    private fun StatusLine(text: String, color: Color, background: Color = CarbonSemanticColors.surface1) {
        Surface(
            modifier = Modifier.fillMaxWidth().border(BorderStroke(1.dp, CarbonSemanticColors.hairline), RoundedCornerShape(0.dp)),
            color = background,
            shape = RoundedCornerShape(0.dp),
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
