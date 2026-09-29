param([ValidateRange(1,65535)][int]$Port = 8000, [switch]$Background)
$ErrorActionPreference = 'Stop'
$taskRepoRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run scripts\setup-demo.ps1 first.' }
if (-not (Test-Path -LiteralPath (Join-Path $taskRepoRoot 'frontend\dist\index.html'))) { throw 'Build the frontend first with scripts\setup-demo.ps1.' }
Set-Location -LiteralPath $taskRepoRoot

function Get-TaskProcessIdentity($Process) {
    $Process.Refresh()
    if ($Process.HasExited) { throw 'The demo process exited before its identity could be recorded.' }
    return @{
        process_id = $Process.Id
        executable = $Process.Path
        start_time_utc = $Process.StartTime.ToUniversalTime().ToString('o')
        start_ticks_utc = $Process.StartTime.ToUniversalTime().Ticks.ToString()
    }
}

if ($Background) {
    $taskWork = Join-Path $taskRepoRoot 'work'
    New-Item -ItemType Directory -Path $taskWork -Force | Out-Null
    $taskExistingListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($taskExistingListener) { throw "Port $Port is already in use. Check the running service before starting another." }
    $taskProcess = Start-Process -FilePath $taskPython -WorkingDirectory $taskRepoRoot -ArgumentList @('-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',"$Port") -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskWork 'demo.stdout.log') -RedirectStandardError (Join-Path $taskWork 'demo.stderr.log')
    # Retain a checked launcher record if startup fails before health is ready.
    @{ process_id = $taskProcess.Id; executable = $taskPython; repository = $taskRepoRoot; port = $Port; started_at = (Get-Date).ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskWork 'demo-server.json') -Encoding UTF8
    $taskLauncherIdentity = Get-TaskProcessIdentity $taskProcess
    $taskReady = $false
    $taskStartupTimer = [System.Diagnostics.Stopwatch]::StartNew()
    while ($taskStartupTimer.Elapsed.TotalSeconds -lt 15) {
        $taskProcess.Refresh()
        if ($taskProcess.HasExited) { throw 'Demo startup failed. See work\demo.stderr.log.' }
        try {
            $taskHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 1
            if ($taskHealth.status -eq 'ok') { $taskReady = $true; break }
        } catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $taskReady) { throw 'Demo startup health check timed out. See work\demo.stderr.log.' }
    $taskServerPid = [int]$taskHealth.process_id
    if ($taskServerPid -lt 1 -or ($taskServerPid -ne $taskProcess.Id -and [int]$taskHealth.parent_process_id -ne $taskProcess.Id)) {
        throw 'The health endpoint did not identify the launched demo process. Nothing else was stopped.'
    }
    $taskServerProcess = Get-Process -Id $taskServerPid -ErrorAction Stop
    $taskServerIdentity = Get-TaskProcessIdentity $taskServerProcess
    $taskListener = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($taskListener.Count -eq 0 -or @($taskListener | Where-Object { $_.OwningProcess -ne $taskServerPid }).Count -gt 0) {
        throw 'The listener does not belong to the launched demo server. Nothing else was stopped.'
    }
    @{
        record_version = 2
        process_id = $taskServerPid
        launcher_process_id = $taskProcess.Id
        executable = $taskPython
        repository = $taskRepoRoot
        port = $Port
        started_at = (Get-Date).ToString('o')
        server_identity = $taskServerIdentity
        launcher_identity = $taskLauncherIdentity
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $taskWork 'demo-server.json') -Encoding UTF8
    Write-Output "Started server process $taskServerPid (launcher $($taskProcess.Id)). Open http://127.0.0.1:$Port . Stop with scripts\stop-demo.ps1."
} else {
    Write-Output "Open http://127.0.0.1:$Port . Stop with Ctrl+C."
    & $taskPython -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
}
