# 启动 AI Hub 调度台（FastAPI，127.0.0.1:4200）
# 用法：在 hub 目录执行 .\start-hub.ps1
# 依赖：复用 ai-relay\.venv（fastapi/uvicorn/httpx）与 ai-relay\.env（LITELLM_MASTER_KEY）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 清掉残留代理变量：网关/适配桥/OAuth 都是本地回环，代理会截胡
$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null
$env:NO_PROXY = "127.0.0.1,localhost"

# 注入 ai-relay\.env（LITELLM_MASTER_KEY 等）
$envFile = Join-Path $root "..\ai-relay\.env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^\s*[^#\s][^=]*=' } | ForEach-Object {
        $pair = $_ -split '=', 2
        [Environment]::SetEnvironmentVariable($pair[0].Trim(), $pair[1].Trim(), "Process")
    }
}

$py = Join-Path $root "..\ai-relay\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "未找到 ai-relay 虚拟环境：$py" -ForegroundColor Yellow
    exit 1
}

Write-Host "AI Hub 启动中 → http://127.0.0.1:4200" -ForegroundColor Green
& $py -m uvicorn hub_server:app --host 127.0.0.1 --port 4200
