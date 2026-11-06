$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
# 先准备独立虚拟环境，再生成 Windows 可执行目录。
python -m venv .codex_tmp/assistant_desktop_build
& ./.codex_tmp/assistant_desktop_build/Scripts/python.exe -m pip install -r desktop_assistant/requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$assistantIcon = (Get-Item -LiteralPath "static/assistant-icon.svg").FullName
& ./.codex_tmp/assistant_desktop_build/Scripts/python.exe -m PyInstaller --noconfirm --windowed --name WarehouseAssistant --add-data ("{0}:static" -f $assistantIcon) --distpath dist --workpath .codex_tmp/assistant_desktop_build_work --specpath .codex_tmp desktop_assistant/main.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output "桌面小助手已生成：dist/WarehouseAssistant/WarehouseAssistant.exe"
