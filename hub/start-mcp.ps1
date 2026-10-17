# 启动 AI Hub MCP Server Facade（127.0.0.1:4300）
# 用法：在 hub 目录执行 .\start-mcp.ps1
# 依赖：复用 ai-relay\.venv（mcp<2 / starlette / uvicorn）
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

Write-Host "MCP Facade 启动中 → http://127.0.0.1:4300  (MCP: /mcp 与 /sse, REST: /api/tasks)" -ForegroundColor Green
& $py -m uvicorn mcp_server:app --host 127.0.0.1 --port 4300
