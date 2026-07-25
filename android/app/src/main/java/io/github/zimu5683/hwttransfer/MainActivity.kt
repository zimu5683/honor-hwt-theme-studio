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
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.DateFormat
import java.util.Date

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
            MaterialTheme {
                Surface(Modifier.fillMaxSize()) { ReceiverScreen() }
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

        Column(
            modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Text("荣耀主题传输助手", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
            Text("无需 Termux。授权一次主题目录后，电脑上的大雪主题编辑器即可直接发送 HWT。")

            Section("主题目录") {
                Text(state.destination, fontWeight = FontWeight.Medium)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { directoryPicker.launch(null) }) { Text("选择 Honor/Themes") }
                    OutlinedButton(onClick = ::grantAllFiles) { Text("全盘权限备用") }
                }
                Text("优先使用目录授权；只有 MagicOS 文件选择器无法授权时才使用全盘权限。",
                    style = MaterialTheme.typography.bodySmall)
            }

            Section("电脑接收") {
                Text(if (state.running) "状态：正在接收" else "状态：未启动", fontWeight = FontWeight.Medium)
                if (state.running) {
                    Text("配对码：${state.pairCode}", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                    if (state.codeExpiresAt > 0) {
                        Text("有效期至 ${DateFormat.getTimeInstance(DateFormat.MEDIUM).format(Date(state.codeExpiresAt))}")
                    }
                    Text("手机地址：${state.addresses.joinToString().ifBlank { "等待网络地址" }}")
                    Text("端口：${Protocol.HTTP_PORT}；30 分钟无活动自动停止")
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { if (state.running) stopReceiver() else requestStartReceiver() }) {
                        Text(if (state.running) "停止接收" else "开始接收")
                    }
                    if (state.running) OutlinedButton(onClick = ::regenerateCode) { Text("刷新配对码") }
                }
            }

            Section("本地导入") {
                Text("也可以先通过荣耀分享、微信或数据线把 HWT 放到手机，再从这里导入。")
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { filePicker.launch(arrayOf("application/octet-stream", "application/zip", "*/*")) }) {
                        Text("选择 HWT 文件")
                    }
                    OutlinedButton(onClick = ::openThemeManager) { Text("打开荣耀主题") }
                }
                Text("最近一次：${state.lastTransfer}")
            }

            Section("已配对电脑") {
                if (state.clients.isEmpty()) {
                    Text("暂无")
                } else {
                    state.clients.forEachIndexed { index, client ->
                        if (index > 0) HorizontalDivider()
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Column { Text(client.name); Text(DateFormat.getDateTimeInstance().format(Date(client.pairedAt)), style = MaterialTheme.typography.bodySmall) }
                            OutlinedButton(onClick = {
                                pairing.revoke(client.tokenHash)
                                refreshState()
                            }) { Text("撤销") }
                        }
                    }
                }
            }

            if (state.error.isNotBlank()) {
                Text("错误：${state.error}", color = MaterialTheme.colorScheme.error)
            }
            Spacer(Modifier.height(20.dp))
            Text("协议 v${Protocol.VERSION} · APK ${BuildConfig.VERSION_NAME}", style = MaterialTheme.typography.bodySmall)
        }
    }

    @Composable
    private fun Section(title: String, content: @Composable () -> Unit) {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                content()
            }
        }
    }
}
