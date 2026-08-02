$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

python -m pip install -r requirements.txt
python -m unittest discover -s tests -v

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --noupx `
  --windowed `
  --name "大雪主题编辑器" `
  --add-data "assets;assets" `
  --exclude-module PySide6.QtWebEngineCore `
  --exclude-module PySide6.QtWebEngineWidgets `
  --exclude-module PySide6.QtWebEngineQuick `
  run.py

Write-Host "构建完成：$Root\dist\大雪主题编辑器.exe"

