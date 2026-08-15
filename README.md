# 大雪主题编辑器

当前版本：`0.2.1`

一个面向荣耀 `.hwt` 主题的中文可视化编辑器。程序只读分析
`30039574_大雪.hwt`，把资源位置整理成目录；新主题从空白模板生成，
只有用户启用的覆盖资源才会写入导出的 HWT。

## 主要功能

0.1.5.1 起桌面端采用 PySide6 Widgets + 集中式 QSS 的 Studio Soft Light 视觉系统；Android
传输助手采用 Jetpack Compose + 自定义 Material 3 Studio Soft Light 主题。两端使用暖灰画布、
紫色主操作、白色 12px 圆角卡片、Pastel 类别点缀和明确的 primary/secondary/tertiary/ghost/danger
语义，不启用 `qt-material`、动态配色或默认 elevation。Windows 使用系统原生标题栏和窗口按钮，
中文字体使用 IBM Plex Sans SC，Inter Variable 用于 Latin 字符和 Android 字体度量。

- 默认“简洁编辑”只显示 30 个明确的中文项目，一次修改自动同步底层兼容资源。
- “高级编辑”保留 1.2 万余个资源槽位、搜索和过滤，技术列默认隐藏并可随时展开。
- 可识别已配对荣耀手机的系统版本和已安装应用，自动隐藏本机不适用的简洁项目。
- 支持颜色、布尔值、文字、普通图片、WebP 和 NinePatch。
- 图片可选择裁剪、完整放入、拉伸、取景位置及亮/暗蒙层。
- 支持批量设置筛选出的颜色，以及同名兼容资源同步。
- 高级模式可以手动增加准确的包名、资源名和目标路径。
- 工程支持撤销、重做、保存和恢复系统默认。
- 保存工程时会把引用图片收集到同名 `.assets` 目录，工程可整体移动和分享。
- 打开工程发现图片缺失时，可更换图片、搜索文件夹或改用灰白占位图。
- 导出前验证外层及嵌套 ZIP、XML、颜色、图片格式及荣耀本地主题识别骨架。
- 重新扫描源主题时会保存 `source_compatibility.report.json`，把源主题可读取兼容性警告与导出文件严格验证分开记录。
- 配套“荣耀主题传输助手”APK，可在局域网内通过 UDP 或有界 HTTP 回退自动发现、配对并流式发送 HWT。
- 手机端通过目录授权写入 `Honor/Themes`，也支持从手机本地选择 HWT 导入。
- 原有 `phone-termux` SSH 发送方式保留在“高级”菜单中作为备用。
- 简洁编辑卡片显示 MagicOS 10.0.0 真机参考图和受影响位置；点击缩略图可对比原始参考与当前颜色/图片合成效果。
- 高级资源类型显示为“颜色、开关、整数、尺寸、文字、图片”等中文，工程 JSON 与 HWT 内部值保持兼容。

## 使用步骤

1. 下载 Windows 版 ZIP，解压后双击 `大雪主题编辑器\大雪主题编辑器.exe`。
2. 在“简洁编辑”顶部展开“主题信息”，填写方案名称和主题身份。
3. 在“简洁编辑”中直接设置壁纸、全局配色、系统界面、桌面或常用应用；需要逐项调整时进入“高级编辑”。
4. 简洁项目会自动同步所有相关兼容资源；可在“修改记录”查看实际影响数量。
5. 点击“导出 HWT”；程序始终生成新文件，不修改大雪源主题。
6. 在手机安装并打开“荣耀主题传输助手”，首次选择 `Honor/Themes`，然后点击“开始接收”。
7. 电脑点击“发送到手机”，选择自动发现的设备；首次输入手机显示的 6 位配对码。
8. 点击“识别手机”可读取型号、MagicOS/Android 版本和主题相关应用列表，信息仅保存在本机配对记录中。
9. 上传和 SHA-256 校验完成后，在手机通知中打开荣耀“主题”，进入“我的 → 下载 → 主题”查找并应用。

未启用的槽位不会写进 HWT，手机继续使用系统默认资源。标记为
“当前版本不支持”的项目（例如微信 8.0.76 主界面图片背景）不会导出。

## 自动更新

桌面端启动后会在后台检查本仓库的 GitHub Release；也可以在“更多 → 检查更新”中手动检查。
新版优先选择 Windows Setup 安装器，下载后会校验发布页提供的 SHA-256，再启动安装向导并退出旧版；
ZIP 便携包继续用于旧版兼容和故障回退。更新源固定为本项目 GitHub Release，不要从其他来源替换
程序文件。

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

真机参考图来自 ADB `exec-out screencap -p` 和隐私空间中的授权截图；发布包只包含缩放图和元数据，原始 PNG/JPG/XML 保存在工作目录外，不进入 Git 或 EXE。

输出程序目录位于 `dist/大雪主题编辑器`；发布时同时生成 Setup 安装器和 ZIP 便携包。

Windows 版先使用 PyInstaller onedir 构建并关闭 UPX 压缩，再用 Inno Setup 生成可自定义路径的当前用户
安装器。默认安装到 `%LOCALAPPDATA%\Programs\HwtThemeStudio`，不需要管理员权限。安装器暂未进行
Authenticode 签名，SmartScreen 可能显示“未知发布者”；请先核对发布页中的 SHA-256，并只从本项目
GitHub Release 下载。

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
