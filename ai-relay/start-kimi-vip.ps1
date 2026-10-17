# 启动 Kimi 会员额度通道：kimi web 本地服务 + OpenAI 兼容适配桥（127.0.0.1:4100）
# 用法：在本目录执行 .\start-kimi-vip.ps1（保持窗口开着；Ctrl+C 停止桥，kimi web 会一并停）
# 前提：已装 Kimi Code CLI 且 kimi login 登录有效
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 清掉残留代理变量，避免 OAuth 刷新被截胡
$env:HTTP_PROXY = $null
$env:HTTPS_PROXY = $null

# 1) 启动 kimi web（若已在跑则复用）
$kimiUp = $false
try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:58628/api/v1/healthz" -UseBasicParsing -TimeoutSec 3
    $kimiUp = $true
} catch {}

if (-not $kimiUp) {
    Write-Host "启动 kimi web ..." -ForegroundColor Cyan
    $proc = Start-Process -FilePath "kimi" -ArgumentList "web --no-open --port 58628" -PassThru -WindowStyle Hidden
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 2
        try {
            $null = Invoke-WebRequest -Uri "http://127.0.0.1:58628/api/v1/healthz" -UseBasicParsing -TimeoutSec 3
            $kimiUp = $true
            break
        } catch {}
    }
    if (-not $kimiUp) { throw "kimi web 启动失败，请手动运行 kimi web --port 58628 查看原因" }
    # 记录进程，退出脚本时一并停掉
    $global:KimiWebProc = $proc
    trap { if ($global:KimiWebProc -and -not $global:KimiWebProc.HasExited) { Stop-Process -Id $global:KimiWebProc.Id -Force }; break }
} else {
    Write-Host "kimi web 已在运行，复用" -ForegroundColor Cyan
}

# 2) 校验登录态
$token = Get-Content (Join-Path $root ".kimi-web-token") -ErrorAction SilentlyContinue
if (-not $token) { throw "未找到 .kimi-web-token，请先跑一次 kimi web 并把 Token 存入该文件" }
$auth = Invoke-WebRequest -Uri "http://127.0.0.1:58628/api/v1/auth" -Headers @{ Authorization = "Bearer $token" } -UseBasicParsing -TimeoutSec 10
$status = ($auth.Content | ConvertFrom-Json).data.managed_provider.status
if ($status -ne "authenticated") { throw "Kimi 登录态为 $status，请先运行 kimi login 重新登录" }
Write-Host "Kimi 登录态正常（OAuth 套餐额度）" -ForegroundColor Green

# 3) 启动适配桥（前台，Ctrl+C 停止）
Write-Host "适配桥启动中 → http://127.0.0.1:4100/v1 （模型：kimi-vip / kimi-vip-hs）" -ForegroundColor Green
& (Join-Path $root ".venv\Scripts\python.exe") (Join-Path $root "kimi_bridge.py")
