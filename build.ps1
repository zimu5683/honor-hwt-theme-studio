$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败，退出码：$LASTEXITCODE" }
python -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw "测试失败，退出码：$LASTEXITCODE" }

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --noupx `
  --windowed `
  --name "大雪主题编辑器" `
  --add-data "assets;assets" `
  --exclude-module PySide6.QtWebEngineCore `
  --exclude-module PySide6.QtWebEngineWidgets `
  --exclude-module PySide6.QtWebEngineQuick `
  run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败，退出码：$LASTEXITCODE" }

if (-not (Test-Path -LiteralPath "$Root\dist\大雪主题编辑器\大雪主题编辑器.exe")) {
    throw "构建结果缺少桌面程序"
}

Write-Host "构建完成：$Root\dist\大雪主题编辑器"
