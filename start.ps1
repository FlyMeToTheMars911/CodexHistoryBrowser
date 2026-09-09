param(
    [int]$Port = 8765,
    [string]$CodexHome = "",
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$candidates = [System.Collections.Generic.List[string]]::new()
# The Codex bundled runtime can be used without installing another Python.
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $bundledPython) { $candidates.Add($bundledPython) }
foreach ($commandName in @('python', 'python3')) {
    $commandInfo = Get-Command $commandName -ErrorAction SilentlyContinue
    if ($commandInfo) { $candidates.Add($commandInfo.Source) }
}
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    $pyLocation = & $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
    if ($LASTEXITCODE -eq 0 -and $pyLocation) { $candidates.Add([string]$pyLocation) }
}
$pythonPath = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    try {
        & $candidate -c "import sys,sqlite3; assert sys.version_info >= (3,10); c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE probe USING fts5(body, tokenize=trigram)')" 2>$null
        if ($LASTEXITCODE -eq 0) { $pythonPath = $candidate; break }
    } catch { continue }
}
if (-not $pythonPath) {
    Write-Host 'Python 3.10+ with SQLite FTS5/trigram support was not found.' -ForegroundColor Red
    Write-Host 'Install a current Python 3 release, then run start.cmd again.'
    exit 1
}
Write-Host "Using Python: $pythonPath" -ForegroundColor DarkGray
$serverArgs = @((Join-Path $PSScriptRoot 'server.py'), '--port', [string]$Port)
if ($CodexHome) { $serverArgs += @('--codex-home', $CodexHome) }
if ($NoBrowser) { $serverArgs += '--no-browser' }
& $pythonPath @serverArgs
exit $LASTEXITCODE
