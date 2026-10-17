# 启动豆包 App 执行器适配桥（OpenAI 兼容，127.0.0.1:4301）
# 用法：在 hub 目录执行 .\start-doubao.ps1（保持窗口开着）
# 依赖：ai-relay\.venv（fastapi/uvicorn）+ winapp CLI + 已登录的豆包桌面端
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 清掉残留代理变量（本地回环必须直连）
$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null
$env:NO_PROXY = "127.0.0.1,localhost"

# winapp 可能在装好后还没进当前会话 PATH，补上
$winapps = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
if ($env:Path -notlike "*$winapps*") { $env:Path += ";$winapps" }

$py = Join-Path $root "..\ai-relay\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "ai-relay venv not found: $py" -ForegroundColor Yellow
    exit 1
}

Write-Host "Doubao bridge starting -> http://127.0.0.1:4301/v1 (model: doubao-app)" -ForegroundColor Green
& $py -m uvicorn doubao_bridge:app --host 127.0.0.1 --port 4301
