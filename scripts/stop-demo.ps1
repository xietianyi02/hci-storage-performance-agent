$ErrorActionPreference = 'Stop'
$taskRepoRoot = Split-Path -Parent $PSScriptRoot
$taskRecord = Join-Path $taskRepoRoot 'work\demo-server.json'
if (-not (Test-Path -LiteralPath $taskRecord)) { Write-Output 'No background demo process record.'; exit 0 }
$taskServer = Get-Content -LiteralPath $taskRecord -Raw | ConvertFrom-Json
if ([IO.Path]::GetFullPath([string]$taskServer.repository) -ne [IO.Path]::GetFullPath($taskRepoRoot)) {
    throw 'The process record belongs to another repository. Nothing was stopped.'
}

function Get-TaskMatchingProcess($Identity) {
    if (-not $Identity -or [int]$Identity.process_id -lt 1) { throw 'Missing demo process identity. Nothing was stopped.' }
    $taskCandidate = Get-Process -Id ([int]$Identity.process_id) -ErrorAction SilentlyContinue
    if (-not $taskCandidate) { return $null }
    $taskCandidate.Refresh()
    if ($taskCandidate.HasExited) { return $null }
    if ($taskCandidate.Path -ne [string]$Identity.executable -or $taskCandidate.StartTime.ToUniversalTime().Ticks.ToString() -ne [string]$Identity.start_ticks_utc) {
        throw 'A recorded PID now belongs to a different process. Nothing was stopped.'
    }
    return $taskCandidate
}

if ($taskServer.record_version -eq 2) {
    # Check captured identities before either termination; reject reused PIDs.
    $taskActualServer = Get-TaskMatchingProcess $taskServer.server_identity
    $taskLauncher = Get-TaskMatchingProcess $taskServer.launcher_identity
    $taskListeners = @(Get-NetTCPConnection -LocalPort ([int]$taskServer.port) -State Listen -ErrorAction SilentlyContinue)
    if (@($taskListeners | Where-Object { $_.OwningProcess -ne [int]$taskServer.server_identity.process_id }).Count -gt 0) {
        throw 'The recorded port belongs to another process. Nothing was stopped.'
    }
    if (-not $taskActualServer -and -not $taskLauncher) { Write-Output 'The recorded demo processes have already stopped.'; exit 0 }
    if ($taskActualServer) {
        Stop-Process -InputObject $taskActualServer
        $taskActualServer.WaitForExit(3000) | Out-Null
    }
    if ([int]$taskServer.launcher_identity.process_id -ne [int]$taskServer.server_identity.process_id) {
        # The launcher normally exits with its child. Recheck if it survives.
        $taskRemainingLauncher = Get-TaskMatchingProcess $taskServer.launcher_identity
        if ($taskRemainingLauncher) { Stop-Process -InputObject $taskRemainingLauncher }
    }
} else {
    # Older Windows venv records only identify the launcher. Query that PID
    # and verify the repository executable and exact server invocation.
    $taskLegacyPid = [int]$taskServer.process_id
    if ($taskLegacyPid -lt 1) { throw 'Invalid legacy process record. Nothing was stopped.' }
    $taskLegacyProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $taskLegacyPid"
    if (-not $taskLegacyProcess) { Write-Output 'The recorded demo launcher has already stopped.'; exit 0 }
    $taskExpectedPython = Join-Path $taskRepoRoot '.venv\Scripts\python.exe'
    $taskExpectedPort = '--port\s+' + [regex]::Escape([string]$taskServer.port) + '(\s|$)'
    if ($taskLegacyProcess.ExecutablePath -ne $taskExpectedPython -or $taskLegacyProcess.CommandLine -notmatch 'uvicorn\s+backend\.main:app' -or $taskLegacyProcess.CommandLine -notmatch '--host\s+127\.0\.0\.1' -or $taskLegacyProcess.CommandLine -notmatch $taskExpectedPort) {
        throw 'The legacy process no longer matches this repository demo. Nothing was stopped.'
    }
    & taskkill.exe /PID $taskLegacyPid /T /F
    if ($LASTEXITCODE -ne 0) { throw 'The verified demo process tree could not be stopped.' }
}
Write-Output 'Background demo stopped. Case data is retained.'
