# 大雪主题编辑器

当前版本：`0.1.2`

一个面向荣耀 `.hwt` 主题的中文可视化编辑器。程序只读分析
`30039574_大雪.hwt`，把资源位置整理成目录；新主题从空白模板生成，
只有用户启用的覆盖资源才会写入导出的 HWT。

## 主要功能

- “常用编辑”按微信、设置、短信、电话、桌面、控制中心等分类跳转。
- “全部资源”展示 1.2 万余个资源槽位并支持搜索和过滤。
- 支持颜色、布尔值、文字、普通图片、WebP 和 NinePatch。
- 图片可选择裁剪、完整放入、拉伸、取景位置及亮/暗蒙层。
- 支持批量设置筛选出的颜色，以及同名兼容资源同步。
- 高级模式可以手动增加准确的包名、资源名和目标路径。
- 工程支持撤销、重做、保存和恢复系统默认。
- 保存工程时会把引用图片收集到同名 `.assets` 目录，工程可整体移动和分享。
- 打开工程发现图片缺失时，可更换图片、搜索文件夹或改用灰白占位图。
- 导出前验证外层及嵌套 ZIP、XML、颜色、图片格式及荣耀本地主题识别骨架。
- 配套“荣耀主题传输助手”APK，可在局域网内自动发现、配对并流式发送 HWT。
- 手机端通过目录授权写入 `Honor/Themes`，也支持从手机本地选择 HWT 导入。
- 原有 `phone-termux` SSH 发送方式保留在“高级”菜单中作为备用。

## 使用步骤

1. 双击 `大雪主题编辑器.exe`。
2. 在“概览”填写方案名称和主题身份。
3. 从“常用编辑”进入某个区域，或在“全部资源”搜索资源。
4. 设置颜色或选择图片，点击“应用修改”。
5. 点击“导出 HWT”；程序始终生成新文件，不修改大雪源主题。
6. 在手机安装并打开“荣耀主题传输助手”，首次选择 `Honor/Themes`，然后点击“开始接收”。
7. 电脑点击“发送到手机”，选择自动发现的设备；首次输入手机显示的 6 位配对码。
8. 上传和 SHA-256 校验完成后，在手机通知中打开荣耀“主题”。
9. 进入“我的 → 下载 → 主题”查找并应用；如该页已打开，返回后重新进入一次以刷新。

未启用的槽位不会写进 HWT，手机继续使用系统默认资源。标记为
“当前版本不支持”的项目（例如微信 8.0.76 主界面图片背景）不会导出。

## 开发运行

```powershell
python -m pip install -r requirements.txt
python run.py
```

## 生成目录与空白主题

```powershell
python tools/build_assets.py `
  "D:\HONOR Share\Honor Share\30039574_大雪.hwt"
```

## 测试与打包

```powershell
python -m unittest discover -s tests -v
./build.ps1
```

输出程序位于 `dist/大雪主题编辑器.exe`。

## Android 传输助手

Android 工程位于 `android/`，支持 Android 13–16（`minSdk 33`、`targetSdk 36`）：

```powershell
cd android
./gradlew.bat testDebugUnitTest lintDebug assembleDebug
```

调试 APK 位于 `android/app/build/outputs/apk/debug/app-debug.apk`。如果仓库位于包含中文的
Windows 路径，APK 可以正常构建；Gradle 单元测试若受 Windows 路径编码影响，可从一个纯英文
目录联接运行，GitHub Actions 的 Linux 构建不受影响。

正式 APK 使用专用 release keystore 签名。GitHub 仓库需要配置以下 Actions Secrets：

- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEY_ALIAS`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_PASSWORD`

可以使用 JDK 的 `keytool` 生成一次性发布密钥，并把 keystore 转为 Base64 后保存到
`ANDROID_KEYSTORE_BASE64`：

```powershell
keytool -genkeypair -keystore hwt-release.jks -alias hwt-transfer `
  -keyalg RSA -keysize 4096 -validity 10000
[Convert]::ToBase64String([IO.File]::ReadAllBytes("hwt-release.jks")) | Set-Clipboard
```

keystore 必须离线备份且不能提交仓库；丢失后将无法覆盖升级已经安装的 APK。协议说明见
[`docs/phone-transfer-protocol.md`](docs/phone-transfer-protocol.md)。

## 许可证

本程序源代码采用 MIT License。使用本工具时，请确保你有权编辑和分发所处理的主题资源。
