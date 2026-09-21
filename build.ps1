# One-click build: pip install -r requirements.txt; powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
# Windowed (no console) tray app. main.pyw is the entry point.
pyinstaller --noconfirm --clean `
  --name "MCP-Manager" `
  --windowed `
  --onedir `
  --add-data "mcp_manager_settings.py;." `
  --add-data "mcp_registry.py;." `
  --add-data "tunnel_manager.py;." `
  --add-data "settings_ui_ctk.py;." `
  --hidden-import customtkinter `
  --hidden-import pystray `
  --hidden-import PIL `
  main.pyw
Write-Host "Built dist/MCP-Manager/MCP-Manager.exe - copy tunnel-client-runtime.exe next to it on first run."
