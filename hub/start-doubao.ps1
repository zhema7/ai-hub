# 启动豆包 App 执行器适配桥（OpenAI 兼容，127.0.0.1:4301，P2 起 CDP 后台后端）
# 用法：在 hub 目录执行 .\start-doubao.ps1（保持窗口开着）
# 依赖：ai-relay\.venv（fastapi/uvicorn）+ 已登录的豆包桌面端
# CDP 后端要求豆包带 --remote-debugging-port=9223 启动：本脚本自动检测，
# 没带参在跑的旧实例会被重启（Electron 单例会吞参数，必须先杀）。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 清掉残留代理变量（本地回环必须直连）
$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null
$env:NO_PROXY = "127.0.0.1,localhost"

# winapp 可能在装好后还没进当前会话 PATH，补上（UIA 后端 fallback 用）
$winapps = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
if ($env:Path -notlike "*$winapps*") { $env:Path += ";$winapps" }

$py = Join-Path $root "..\ai-relay\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ai-relay venv not found: $py" -ForegroundColor Yellow
    exit 1
}

# 豆包带 CDP 调试口拉起
$cdp = Get-NetTCPConnection -LocalPort 9223 -State Listen -ErrorAction SilentlyContinue
if (-not $cdp) {
    $exe = Join-Path $env:LOCALAPPDATA "Doubao\Application\app\Doubao.exe"
    if (-not (Test-Path $exe)) { Write-Host "未找到 $exe" -ForegroundColor Yellow; exit 1 }
    Get-Process Doubao -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 3
    Start-Process $exe -ArgumentList "--remote-debugging-port=9223"
    Write-Host "Doubao 已带 --remote-debugging-port=9223 拉起，等待 CDP 就绪..." -ForegroundColor Cyan
    $ok = $false
    foreach ($i in 1..30) {
        Start-Sleep -Seconds 2
        if (Get-NetTCPConnection -LocalPort 9223 -State Listen -ErrorAction SilentlyContinue) { $ok = $true; break }
    }
    if (-not $ok) { Write-Host "9223 未监听（带参启动失败，可改回 UIA 后端）" -ForegroundColor Yellow; exit 1 }
} else {
    Write-Host "CDP 9223 已在监听" -ForegroundColor Cyan
}

Write-Host "Doubao bridge starting -> http://127.0.0.1:4301/v1 (model: doubao-app, CDP backend)" -ForegroundColor Green
& $py -m uvicorn doubao_bridge:app --host 127.0.0.1 --port 4301
