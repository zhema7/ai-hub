# AI Hub Collab · 打包 + 发布 VS Code Marketplace 一键脚本
# 用法：
#   首次发布（创建 publisher，PAT 只需要一次）：.\publish.ps1 -Pat <PAT> -Create
#   日常发布（版本号自动 +0.0.1）：             .\publish.ps1 -Pat <PAT>
#   只打包不发布：                              .\publish.ps1
# PAT 获取：https://dev.azure.com → User settings → Personal Access Tokens
#           Organization = All accessible，Scopes = Marketplace → Manage，有效期选最长
param(
    [string]$Pat,
    [switch]$Create,
    [string]$Bump   # major | minor | patch，默认 patch
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 走代理拉 vsce（本机直连 npm registry 会超时）
$env:HTTP_PROXY = "http://127.0.0.1:7993"
$env:HTTPS_PROXY = "http://127.0.0.1:7993"

$icon = Join-Path $root "icon.png"
if (-not (Test-Path $icon)) {
    Write-Host "生成 icon.png..." -ForegroundColor Cyan
    & powershell -ExecutionPolicy Bypass -File (Join-Path $root "make_icon.ps1")
}

Write-Host "打包..." -ForegroundColor Cyan
npx --yes @vscode/vsce package --allow-missing-repository
if ($LASTEXITCODE -ne 0) { Write-Host "打包失败" -ForegroundColor Red; exit 1 }

if (-not $Pat) {
    Write-Host "未提供 PAT，仅完成本地打包。" -ForegroundColor Yellow
    exit 0
}

if ($Create) {
    Write-Host "创建 publisher zhema7..." -ForegroundColor Cyan
    npx --yes @vscode/vsce create-publisher zhema7 -p $Pat
    if ($LASTEXITCODE -ne 0) { Write-Host "创建 publisher 失败（可能已存在，忽略此错误继续）" -ForegroundColor Yellow }
}

$vsceArgs = @("--yes", "@vscode/vsce", "publish", "-p", $Pat)
if ($Bump) { $vsceArgs += $Bump }
npx @vscode/vsce @vsceArgs
if ($LASTEXITCODE -eq 0) {
    Write-Host "发布成功：https://marketplace.visualstudio.com/items?itemName=zhema7.ai-hub-collab" -ForegroundColor Green
} else {
    Write-Host "发布失败——检查 PAT 权限（Marketplace > Manage）与 publisher 是否匹配" -ForegroundColor Red
    exit 1
}
