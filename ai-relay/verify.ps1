# 验证中转站统一入口（填完 .env 里的 API Key 后执行）
# 用法：.\verify.ps1   （需先另开窗口跑 .\start.ps1）
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^\s*[^#\s][^=]*=' } | ForEach-Object {
        $p = $_ -split '=', 2
        [Environment]::SetEnvironmentVariable($p[0].Trim(), $p[1].Trim(), "Process")
    }
}
$h = @{ Authorization = "Bearer $($env:LITELLM_MASTER_KEY)"; "Content-Type" = "application/json" }
$base = "http://127.0.0.1:4000"

function Test-Endpoint($name, $url, $body) {
    try {
        $r = Invoke-WebRequest -Uri $url -Method POST -Headers $h -Body $body -UseBasicParsing -TimeoutSec 60
        Write-Host "[OK]   $name  HTTP $($r.StatusCode)" -ForegroundColor Green
        Write-Host "       $($r.Content)"
    } catch {
        $resp = $_.Exception.Response
        if ($resp) {
            Write-Host "[FAIL] $name  HTTP $([int]$resp.StatusCode)" -ForegroundColor Red
            $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
            Write-Host "       $($sr.ReadToEnd())"
        } else {
            Write-Host "[FAIL] $name  $($_.Exception.Message)" -ForegroundColor Red
        }
    }
}

# 0) 服务是否活着
try {
    $r = Invoke-WebRequest -Uri "$base/health/liveliness" -UseBasicParsing -TimeoutSec 5
    Write-Host "[OK]   服务存活" -ForegroundColor Green
} catch {
    Write-Host "服务未启动 —— 请先在另一个窗口执行 .\start.ps1" -ForegroundColor Yellow
    return
}

# 1) 档位清单
Write-Host "`n--- 统一入口档位 ---" -ForegroundColor Cyan
Write-Host (Invoke-WebRequest -Uri "$base/v1/models" -Headers $h -UseBasicParsing -TimeoutSec 10).Content

# 2) 逐档位打一次真实请求
Write-Host "`n--- OpenAI 兼容端点 ---" -ForegroundColor Cyan
foreach ($t in @("cheap", "coding", "think", "long")) {
    $b = '{"model":"' + $t + '","messages":[{"role":"user","content":"回答一个词：1+1=?"}],"max_tokens":8}'
    Test-Endpoint $t "$base/v1/chat/completions" $b
}

# 3) Anthropic 兼容端点（Claude Code 走这条）
Write-Host "`n--- Anthropic 兼容端点 ---" -ForegroundColor Cyan
$ba = '{"model":"coding","max_tokens":8,"messages":[{"role":"user","content":"回答一个词：1+1=?"}]}'
Test-Endpoint "messages" "$base/v1/messages" $ba
