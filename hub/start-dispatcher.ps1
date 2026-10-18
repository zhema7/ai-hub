# 启动 AI Hub P4 调度守护（能力路由执行器）
# 用法：在 hub 目录执行 .\start-dispatcher.ps1 [-Interval 5]
# 依赖：复用 ai-relay\.venv；读 routing.yaml + board.db，通道走 :4100/:4301/:4000
param([double]$Interval = 5)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 本地回环服务，清掉残留代理变量避免意外截胡
$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null
$env:NO_PROXY = "127.0.0.1,localhost"

$py = Join-Path $root "..\ai-relay\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "未找到 ai-relay 虚拟环境：$py" -ForegroundColor Yellow
    exit 1
}

Write-Host "P4 dispatcher 启动 → 轮询 board.db 每 ${Interval}s（Ctrl+C 退出）" -ForegroundColor Green
& $py dispatcher.py --interval $Interval
