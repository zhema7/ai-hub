# 启动 AI Hub MCP Server Facade（127.0.0.1:4300）
# 用法：在 hub 目录执行 .\start-mcp.ps1
# 依赖：hub\.venv（mcp<2 独立环境——LiteLLM 要 mcp 2.x，与 ai-relay venv 互斥，故独立）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 本地回环服务，清掉残留代理变量避免意外截胡
$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null
$env:NO_PROXY = "127.0.0.1,localhost"

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "未找到 hub 虚拟环境：$py（先 python -m venv hub\.venv 再装 mcp<2 fastapi starlette uvicorn httpx pydantic）" -ForegroundColor Yellow
    exit 1
}

Write-Host "MCP Facade starting -> http://127.0.0.1:4300  (MCP: /mcp and /sse, REST: /api/tasks)" -ForegroundColor Green
& $py -m uvicorn mcp_server:app --host 127.0.0.1 --port 4300
