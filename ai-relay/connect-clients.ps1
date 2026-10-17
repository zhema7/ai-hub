# 把下游客户端连接到本机 ai-relay 中转站
# 用法：在本目录执行 .\connect-clients.ps1
# 作用：
#   1) 安装 Codex profile（%USERPROFILE%\.codex\relay.config.toml，不覆盖现有 config.toml）
#   2) 设置用户级环境变量 RELAY_API_KEY = 中转站主密钥
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 从 .env 读主密钥
$master = $null
foreach ($line in Get-Content (Join-Path $root ".env")) {
    if ($line -match '^\s*LITELLM_MASTER_KEY\s*=\s*(\S+)\s*$') { $master = $Matches[1] }
}
if (-not $master) { throw ".env 中未找到 LITELLM_MASTER_KEY" }

# 1) Codex profile（非破坏性：不动你现有的 config.toml）
$codexDir = Join-Path $env:USERPROFILE ".codex"
if (-not (Test-Path $codexDir)) { New-Item -ItemType Directory -Path $codexDir | Out-Null }
$dst = Join-Path $codexDir "relay.config.toml"
Copy-Item (Join-Path $root "clients\relay.config.toml") $dst -Force
Write-Host "[OK] Codex profile 已安装: $dst" -ForegroundColor Green

$codexCmd = Get-Command codex -ErrorAction SilentlyContinue
if ($codexCmd) {
    Write-Host "     检测到 Codex: $($codexCmd.Source)"
} else {
    Write-Host "     [提示] 未检测到 codex 命令，装完 Codex CLI 后本配置直接生效" -ForegroundColor Yellow
}

# 2) RELAY_API_KEY：用户级持久环境变量（新终端自动有；当前窗口也立即注入）
[Environment]::SetEnvironmentVariable("RELAY_API_KEY", $master, "User")
$env:RELAY_API_KEY = $master
Write-Host "[OK] 已设置用户环境变量 RELAY_API_KEY（新终端自动生效）" -ForegroundColor Green

Write-Host ""
Write-Host "===== 接下来的使用方式 =====" -ForegroundColor Cyan
Write-Host "  Codex CLI ：先 .\start.ps1 起中转站，再运行 codex --profile relay"
Write-Host "             会话里用 /model 在 cheap / coding / think / long 间切换"
Write-Host "  WorkBuddy ：设置 → 模型 → 自定义模型（OpenAI 兼容），填："
Write-Host "             接口地址  http://127.0.0.1:4000/v1/chat/completions"
Write-Host "             API Key   $master"
Write-Host "             模型名称  逐个添加 cheap / coding / think / long"
