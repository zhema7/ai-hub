# 启动个人 AI 中转站（LiteLLM Proxy）
# 用法：在本目录执行 .\start.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 清掉代理变量：本地回环（适配桥 4100）必须直连，残留的 7993 代理会把请求截胡
$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null
$env:NO_PROXY = "127.0.0.1,localhost"

$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Write-Host "未找到 .env —— 请先执行：Copy-Item .env.example .env，再填入 API Key" -ForegroundColor Yellow
    exit 1
}

# 把 .env 里的键值注入当前进程环境
Get-Content $envFile | Where-Object { $_ -match '^\s*[^#\s][^=]*=' } | ForEach-Object {
    $pair = $_ -split '=', 2
    [Environment]::SetEnvironmentVariable($pair[0].Trim(), $pair[1].Trim(), "Process")
}

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "未找到虚拟环境，请先执行：python -m venv .venv; .\.venv\Scripts\pip install 'litellm[proxy]'" -ForegroundColor Yellow
    exit 1
}

# 注意：用 python -m 方式启动，不依赖 litellm.exe（venv 移动位置后 .exe 启动器会失效）
Write-Host "中转站启动中 → http://127.0.0.1:4000" -ForegroundColor Green
& $py -m litellm.proxy.proxy_cli --config (Join-Path $root "config.yaml") --host 127.0.0.1 --port 4000
