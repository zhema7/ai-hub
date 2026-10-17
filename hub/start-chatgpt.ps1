# 启动 AI Hub ChatGPT App 通道（CDP 9222 + OpenAI 兼容桥 4302）
# 用法：在 hub 目录执行 .\start-chatgpt.ps1
# 前提：ChatGPT 桌面端（MSIX: OpenAI.Codex）须带 --remote-debugging-port=9222 启动。
#       本脚本自动检测：9222 已开 → 直接起桥；没开 → 重启 ChatGPT 带参拉起。
# 注意：Electron 单例——不带参的旧实例在跑时，新进程参数会被吞。所以必须先杀旧实例。
param()
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 本地回环服务，清掉残留代理变量避免意外截胡
$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null
$env:NO_PROXY = "127.0.0.1,localhost"

$py = Join-Path $root "..\ai-relay\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "未找到 ai-relay 虚拟环境：$py" -ForegroundColor Yellow; exit 1 }

# 1) ChatGPT 带调试口拉起
$cdp = Get-NetTCPConnection -LocalPort 9222 -State Listen -ErrorAction SilentlyContinue
if (-not $cdp) {
    $pkg = Get-AppxPackage OpenAI.Codex
    if (-not $pkg) { Write-Host "未安装 ChatGPT 桌面端（MSIX OpenAI.Codex）" -ForegroundColor Yellow; exit 1 }
    $exe = Join-Path $pkg.InstallLocation "app\ChatGPT.exe"
    if (-not (Test-Path $exe)) { Write-Host "未找到 $exe" -ForegroundColor Yellow; exit 1 }
    Get-Process ChatGPT -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2
    Start-Process -FilePath $exe -ArgumentList "--remote-debugging-port=9222"
    Write-Host "ChatGPT 已带 --remote-debugging-port=9222 拉起，等待 CDP 就绪..." -ForegroundColor Cyan
    $ok = $false
    foreach ($i in 1..30) {
        Start-Sleep -Seconds 2
        if (Get-NetTCPConnection -LocalPort 9222 -State Listen -ErrorAction SilentlyContinue) { $ok = $true; break }
    }
    if (-not $ok) { Write-Host "9222 未监听（MSIX 带参启动失败？）" -ForegroundColor Yellow; exit 1 }
} else {
    Write-Host "CDP 9222 已在监听" -ForegroundColor Cyan
}

# 2) 起 OpenAI 兼容桥
Write-Host "ChatGPT 桥启动中 → http://127.0.0.1:4302 (模型 chatgpt-app)" -ForegroundColor Green
& $py -m uvicorn chatgpt_bridge:app --host 127.0.0.1 --port 4302
