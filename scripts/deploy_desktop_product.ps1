$ErrorActionPreference = 'Stop'
$a2nWorkspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $a2nWorkspace
$a2nPython = Join-Path $a2nWorkspace '.venv\Scripts\python.exe'
$a2nBundle = Join-Path $a2nWorkspace 'artifacts\A2N.exe'
$a2nHome = (& $a2nPython -c 'from a2n_node.product_cli import default_home; print(default_home())').Trim()
if ($LASTEXITCODE -ne 0 -or -not [IO.Path]::IsPathRooted($a2nHome)) { throw 'Cannot resolve the canonical node home.' }
$a2nDigest = (Get-FileHash -LiteralPath $a2nBundle -Algorithm SHA256).Hash.ToLowerInvariant()
$a2nTargetRoot = Join-Path $env:LOCALAPPDATA ('A2N\app\' + $a2nDigest.Substring(0,16))
New-Item -ItemType Directory -Path $a2nTargetRoot -Force | Out-Null
$a2nDestination = Join-Path $a2nTargetRoot 'A2N.exe'
Copy-Item -LiteralPath $a2nBundle -Destination $a2nDestination -Force
if ((Get-FileHash -LiteralPath $a2nDestination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $a2nDigest) { throw 'Installed program hash mismatch.' }
# The app may virtualize LocalAppData. Persist the physical path that a
# scheduled task outside the app can read, rather than its virtual alias.
$a2nDestination = (& $a2nPython -c 'import sys; from pathlib import Path; print(Path(sys.argv[1]).resolve())' $a2nDestination).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the physical installation path.' }
$a2nTask = Get-ScheduledTask -TaskName A2N-Node -ErrorAction SilentlyContinue
$a2nTaskSuspended = $false
$a2nPreviousTask = Join-Path $a2nWorkspace '.tmp\before-vision-desktop-task.xml'
if ($a2nTask) {
    Export-ScheduledTask -TaskName A2N-Node | Set-Content -LiteralPath $a2nPreviousTask -Encoding Unicode
    try {
        Disable-ScheduledTask -TaskName A2N-Node -ErrorAction Stop | Out-Null
        $a2nTaskSuspended = $true
        Stop-ScheduledTask -TaskName A2N-Node -ErrorAction Stop
    } catch {
        Write-Output 'The existing task is protected; its launcher will hand over to the bundled SDK.'
    }
}
$a2nHealthArgs = @('-c','import sys; from a2n_node.product_cli import health,default_home; sys.exit(0 if health(8771,default_home()) else 1)')
try {
    & $a2nPython @a2nHealthArgs
    if ($LASTEXITCODE -eq 0) {
        & $a2nPython -m a2n_node.product_cli stop --home $a2nHome --port 8771
        if ($LASTEXITCODE -ne 0) { throw 'Cannot stop the previous node.' }
    }
    & $a2nPython -c 'import sys; from a2n_node.home import wait_for_home_lock; lock=wait_for_home_lock(sys.argv[1],20); lock.close()' $a2nHome
    if ($LASTEXITCODE -ne 0) { throw 'The previous node has not released its home.' }
    $a2nInstallation = @{executable=$a2nDestination; sha256=$a2nDigest; home=$a2nHome; port=8771; runtime_bundled=$true; autostart=$true}
    [IO.File]::WriteAllText((Join-Path $a2nHome 'installation.json'), ($a2nInstallation | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    & $a2nPython -c 'import sys,json; from a2n_node.autostart import enable; print(json.dumps(enable(sys.argv[1],8771,executable=sys.argv[2])))' $a2nHome $a2nDestination
    if ($LASTEXITCODE -ne 0) { throw 'Cannot register the complete SDK startup task.' }
    & $a2nPython -c 'import sys; from a2n_node.product_cli import start_background; start_background(sys.argv[1],8771,[],executable=sys.argv[2])' $a2nHome $a2nDestination
    if ($LASTEXITCODE -ne 0) { throw 'Complete bundled SDK failed to start.' }
    & $a2nPython scripts\verify_desktop_runtime.py $a2nHome $a2nDigest --activate
    if ($LASTEXITCODE -ne 0) { throw 'Listener is not the expected complete SDK program.' }
    try { Start-ScheduledTask -TaskName A2N-Node -ErrorAction Stop } catch { Write-Output 'Current-user logon entry is configured.' }
    Write-Output "Full desktop SDK running from $a2nDestination, home $a2nHome"
} catch {
    if ($a2nTaskSuspended -and (Test-Path -LiteralPath $a2nPreviousTask)) {
        & schtasks /Create /TN A2N-Node /XML $a2nPreviousTask /F | Out-Null
        Enable-ScheduledTask -TaskName A2N-Node | Out-Null
        Start-ScheduledTask -TaskName A2N-Node
    }
    throw
}
