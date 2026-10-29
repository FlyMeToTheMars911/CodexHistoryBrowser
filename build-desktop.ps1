param(
    [string]$PythonPath = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$candidates = [System.Collections.Generic.List[string]]::new()
if ($PythonPath) { $candidates.Add($PythonPath) }
else {
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
}
$buildPython = $null
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    try {
        & $candidate -c "import sys,sqlite3,venv; assert sys.version_info >= (3,10); c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE probe USING fts5(body, tokenize=trigram)')" 2>$null
        if ($LASTEXITCODE -eq 0) { $buildPython = $candidate; break }
    } catch { continue }
}
if (-not $buildPython) { throw 'Python 3.10+ with venv and SQLite FTS5/trigram is required to build the EXE.' }
$venvDir = Join-Path $PSScriptRoot '.venv-build'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $buildPython -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated build environment.' }
}
& $venvPython -m pip install --requirement (Join-Path $PSScriptRoot 'requirements-desktop.txt')
if ($LASTEXITCODE -ne 0) { throw 'Desktop build dependency installation failed.' }
& $venvPython -m PyInstaller --noconfirm (Join-Path $PSScriptRoot 'CodexHistoryBrowser.spec')
if ($LASTEXITCODE -ne 0) { throw 'Desktop EXE packaging failed.' }
Write-Host "EXE: $(Join-Path $PSScriptRoot 'dist\CodexHistoryBrowser.exe')" -ForegroundColor Green
