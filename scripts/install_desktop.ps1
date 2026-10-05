# One entry for the complete Windows desktop product. No administrator shell required.
param([switch]$NoLaunch, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
$a2nRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $a2nRoot

function Find-A2NPython {
    $a2nCandidates = @((Join-Path $a2nRoot '.venv\Scripts\python.exe'))
    $a2nCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($a2nCommand -and $a2nCommand.Source -notlike '*WindowsApps*') { $a2nCandidates += $a2nCommand.Source }
    $a2nCandidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    foreach ($a2nCandidate in $a2nCandidates) {
        if (Test-Path -LiteralPath $a2nCandidate) {
            & $a2nCandidate -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $a2nCandidate }
        }
    }
    $a2nLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($a2nLauncher) {
        $a2nPath = & $a2nLauncher.Source -3 -c 'import sys; sys.exit(1) if sys.version_info < (3,11) else None; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $a2nPath -and (Test-Path -LiteralPath $a2nPath)) { return $a2nPath }
    }
    return $null
}

$a2nPython = Find-A2NPython
if (-not $a2nPython) {
    if ($DryRun) { Write-Host 'Plan: install Python 3.13 per user, then install all A2N packages and launch the console.'; exit 0 }
    $a2nWinget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $a2nWinget) { throw 'Python 3.11+ or Windows App Installer (winget) is required. Install one, then rerun install.bat.' }
    Write-Host 'Installing Python 3.13 for this user...'
    & $a2nWinget.Source install --id Python.Python.3.13 --exact --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
    $a2nPython = Find-A2NPython
    if (-not $a2nPython) { throw 'Python was installed but could not be located. Restart this installer.' }
}
$a2nArguments = @('scripts\bootstrap.py')
if ($DryRun) { $a2nArguments += '--dry-run' }
if (-not $NoLaunch) { $a2nArguments += '--launch' }
& $a2nPython @a2nArguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
