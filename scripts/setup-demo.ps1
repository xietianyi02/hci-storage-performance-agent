param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$taskRepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRepoRoot

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable" }
}

if (-not $Python) {
    $taskPythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($taskPythonCommand) { $Python = $taskPythonCommand.Source }
    else {
        $taskBundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $taskBundledPython) { $Python = $taskBundledPython }
        else { throw 'Python 3.11+ is required. Pass -Python with its executable path.' }
    }
}
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    Invoke-Checked -Executable $Python -Arguments @('-m', 'venv', '.venv')
}
$taskVenvPython = Join-Path $taskRepoRoot '.venv\Scripts\python.exe'
$taskRequirements = if (Test-Path -LiteralPath 'backend\requirements.lock.txt') { 'backend\requirements.lock.txt' } else { 'backend\requirements.txt' }
Invoke-Checked -Executable $taskVenvPython -Arguments @('-m', 'pip', 'install', '-r', $taskRequirements)

$taskPnpmCommand = Get-Command pnpm,pnpm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1
if ($taskPnpmCommand) { $taskPnpm = $taskPnpmCommand.Source }
else {
    $taskPnpm = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd'
    if (-not (Test-Path -LiteralPath $taskPnpm)) { throw 'Node.js and pnpm are required to build the frontend.' }
}
Push-Location -LiteralPath (Join-Path $taskRepoRoot 'frontend')
try {
    Invoke-Checked -Executable $taskPnpm -Arguments @('install', '--frozen-lockfile')
    Invoke-Checked -Executable $taskPnpm -Arguments @('build')
} finally { Pop-Location }
Write-Output 'Setup complete. Run .\scripts\start-demo.ps1'
