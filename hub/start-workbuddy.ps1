# AI Hub - start WorkBuddy desktop (CDP 9224) + OpenAI bridge (4303)
# Usage: powershell -ExecutionPolicy Bypass -File start-workbuddy.ps1
$ErrorActionPreference = "Stop"
$env:HTTP_PROXY = $null; $env:HTTPS_PROXY = $null; $env:NO_PROXY = "127.0.0.1,localhost"

Write-Output "[1/3] stop old WorkBuddy instances"
Get-Process WorkBuddy -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3

Write-Output "[2/3] launch WorkBuddy with --remote-debugging-port=9224"
Start-Process "C:\Program Files\WorkBuddy\WorkBuddy.exe" -ArgumentList "--remote-debugging-port=9224"
$ok = $false
foreach ($i in 1..15) {
    Start-Sleep -Seconds 2
    if (Test-NetConnection -ComputerName 127.0.0.1 -Port 9224 -WarningAction SilentlyContinue -InformationLevel Quiet) { $ok = $true; break }
}
if (-not $ok) { Write-Output "WARN: CDP 9224 not listening yet (app may still be booting)" } else { Write-Output "CDP 9224 ready" }

Write-Output "[3/3] start workbuddy bridge on 4303 (foreground, Ctrl+C to stop)"
Set-Location $PSScriptRoot
& "..\ai-relay\.venv\Scripts\python.exe" workbuddy_bridge.py
