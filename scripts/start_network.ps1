param([string]$HostAddress, [string]$ServerBase, [switch]$LocalOnly, [switch]$RestartDesktop, [switch]$Open)
$ErrorActionPreference = 'Stop'
$a2nRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $a2nRoot
$a2nPython = Join-Path $a2nRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $a2nPython)) { throw 'Run install.bat first.' }
$a2nSettingsFile = Join-Path $a2nRoot '.tmp\network-settings.json'
if ($LocalOnly -and $ServerBase) { throw 'ServerBase and LocalOnly cannot be combined.' }
if ($LocalOnly) {
    $ServerBase = ''
} elseif (-not $PSBoundParameters.ContainsKey('ServerBase') -and (Test-Path -LiteralPath $a2nSettingsFile)) {
    $ServerBase = (Get-Content -LiteralPath $a2nSettingsFile -Raw | ConvertFrom-Json).server_base
}
$a2nServerAllow = ''
if ($ServerBase) {
    $a2nServerURI = $null
    if (-not [Uri]::TryCreate($ServerBase, [UriKind]::Absolute, [ref]$a2nServerURI) -or
        $a2nServerURI.Scheme -notin @('http','https') -or $a2nServerURI.UserInfo -or
        $a2nServerURI.AbsolutePath -ne '/' -or $a2nServerURI.Query -or $a2nServerURI.Fragment) {
        throw 'ServerBase must be an HTTP(S) origin without credentials, paths, query, or fragment.'
    }
    $ServerBase = $a2nServerURI.GetLeftPart([UriPartial]::Authority)
    if ($a2nServerURI.Scheme -eq 'http') {
        $a2nServerIP = $null
        if (-not [Net.IPAddress]::TryParse($a2nServerURI.Host, [ref]$a2nServerIP) -or
            $a2nServerIP.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
            throw 'An HTTP trial server must use a literal IPv4 address; use HTTPS for domain names.'
        }
        $a2nServerAllow = ",$a2nServerIP/32"
    }
}
if (-not $HostAddress) {
    $a2nInterfaces = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.HardwareInterface }
    $HostAddress = $a2nInterfaces | ForEach-Object { $_.IPv4Address.IPAddress } | Where-Object { $_ -match '^(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)' } | Select-Object -First 1
}
$a2nParsedIP = $null
if (-not [Net.IPAddress]::TryParse($HostAddress, [ref]$a2nParsedIP) -or $a2nParsedIP.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'Specify the current LAN IPv4 address with -HostAddress.' }
if (-not (Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -eq $HostAddress)) { throw 'HostAddress must belong to this computer.' }
$a2nEnvironment = Join-Path $a2nRoot '.tmp\acceptance.env'
if (-not (Test-Path -LiteralPath $a2nEnvironment)) { throw 'Create the initial Docker nodes with scripts/docker_acceptance.py first; its existing storage key is required.' }
$a2nEnvironmentText = (Get-Content -LiteralPath $a2nEnvironment -Raw).TrimEnd() + "`nA2N_NETWORK_HOST=$HostAddress`nA2N_NETWORK_SERVER_BASE=$ServerBase`nA2N_NETWORK_SERVER_ALLOW=$a2nServerAllow`n"
[IO.File]::WriteAllText((Join-Path $a2nRoot '.tmp\network.env'), $a2nEnvironmentText, (New-Object System.Text.UTF8Encoding($false)))
$a2nDesktopHome = & $a2nPython -c 'from a2n_node.product_cli import default_home; print(default_home())'
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the configured desktop SDK home.' }
$a2nHealthArgs = @('-c', 'import sys; from a2n_node.product_cli import health,default_home; sys.exit(0 if health(8771, default_home()) else 1)')
& $a2nPython @a2nHealthArgs
$a2nDesktopRunning = $LASTEXITCODE -eq 0
if ($a2nDesktopRunning) {
    $a2nSnapshot = & $a2nPython -m a2n_node.product_cli request --path /v1/runtime
    $a2nState = $a2nSnapshot | ConvertFrom-Json
    $a2nConfiguredRelay = [string]$a2nState.public_service.relay_provider.node
    $a2nCorrectNetwork = $a2nState.public_service.directory_url -eq "http://${HostAddress}:18885/public/v1/agents" -and $a2nConfiguredRelay -eq [string]$ServerBase
    if (-not $RestartDesktop -and -not $a2nCorrectNetwork) {
        throw 'Desktop SDK is running with different network settings. Use -RestartDesktop to apply this network.'
    }
}
# Persist before stopping: the scheduled supervisor may restart the node immediately.
New-Item -ItemType Directory -Path $a2nDesktopHome -Force | Out-Null
$a2nSavedSettings = @{host_address=[string]$HostAddress; server_base=[string]$ServerBase} | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $a2nDesktopHome 'network.json'), $a2nSavedSettings, (New-Object System.Text.UTF8Encoding($false)))
[IO.File]::WriteAllText($a2nSettingsFile, $a2nSavedSettings, (New-Object System.Text.UTF8Encoding($false)))
if ($a2nDesktopRunning -and $RestartDesktop) {
    $a2nOldPid = (Get-NetTCPConnection -State Listen -LocalPort 8771 | Select-Object -First 1).OwningProcess
    & $a2nPython -m a2n_node.product_cli stop
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the existing desktop node.' }
    # Scheduled processes may not allow Wait-Process from this session.
    $a2nStopDeadline = (Get-Date).AddSeconds(20)
    do {
        $a2nOldListening = Get-NetTCPConnection -State Listen -LocalPort 8771 -ErrorAction SilentlyContinue | Where-Object OwningProcess -eq $a2nOldPid
        if (-not $a2nOldListening) { break }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $a2nStopDeadline)
    if ($a2nOldListening) { throw 'Desktop node has not finished stopping.' }
}
& docker compose -f docker/acceptance.yaml -f docker/network.yaml --env-file .tmp/network.env up -d --no-build
if ($LASTEXITCODE -ne 0) { throw 'Docker node startup failed.' }
& $a2nPython @a2nHealthArgs
if ($LASTEXITCODE -ne 0) {
    $a2nProcess = Start-Process -FilePath $a2nPython -ArgumentList '-u','scripts/serve_desktop_node.py' -WorkingDirectory $a2nRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $a2nDesktopHome 'network-out.log') -RedirectStandardError (Join-Path $a2nDesktopHome 'network-error.log')
    $a2nDeadline = (Get-Date).AddSeconds(15)
    do {
        & $a2nPython @a2nHealthArgs
        if ($LASTEXITCODE -eq 0) { break }
        if ($a2nProcess.HasExited) { throw "Desktop startup failed; see $a2nDesktopHome\network-error.log" }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $a2nDeadline)
    if ($LASTEXITCODE -ne 0) { throw 'Desktop node did not become ready.' }
}
Write-Output "Four local nodes are running. Desktop console: http://127.0.0.1:8771/console"
if ($ServerBase) { Write-Output "Server mailbox and sealed relay configured: $ServerBase. Verify remote registration and calls with network_acceptance.py before declaring five nodes online." }
if ($Open) { Start-Process 'http://127.0.0.1:8771/console' }
