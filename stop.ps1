param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) { Write-Host "No service is listening on port $Port."; exit 0 }
$expectedScript = Join-Path $PSScriptRoot 'server.py'
foreach ($entry in $listener) {
    $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($entry.OwningProcess)"
    if (-not $serviceProcess.CommandLine -or $serviceProcess.CommandLine.IndexOf($expectedScript, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        Write-Host "Port $Port belongs to another process. Nothing was stopped." -ForegroundColor Red
        exit 1
    }
    Stop-Process -Id $serviceProcess.ProcessId
    Write-Host "Codex History Browser stopped (PID $($serviceProcess.ProcessId))."
}
