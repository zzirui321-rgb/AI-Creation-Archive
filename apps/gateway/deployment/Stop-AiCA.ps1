param([string]$DeploymentDirectory = (Join-Path $env:LOCALAPPDATA 'AiCA/lan-deployment'))
$ErrorActionPreference = 'Stop'
$statePath = Join-Path $DeploymentDirectory 'service.json'
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$process = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
if (-not $process) { Write-Output 'AiCA is already stopped.'; return }
$recordedStart = if ($state.started_at -is [DateTime]) { $state.started_at.ToUniversalTime() } else { [DateTimeOffset]::Parse($state.started_at).UtcDateTime }
if ($process.Path -ne $state.python -or [Math]::Abs(($process.StartTime.ToUniversalTime() - $recordedStart).TotalSeconds) -gt 1) { throw 'PID no longer identifies this AiCA process; refusing to stop it.' }
Stop-Process -Id $process.Id
if (-not $process.WaitForExit(10000)) { throw 'AiCA did not stop within 10 seconds.' }
$workerRoot = Join-Path $state.data 'execution-workers'
if (Test-Path -LiteralPath $workerRoot) {
    foreach ($record in Get-ChildItem -LiteralPath $workerRoot -Filter 'worker-process.json' -File -Recurse) {
        $workerState = Get-Content -LiteralPath $record.FullName -Raw | ConvertFrom-Json
        $worker = Get-Process -Id $workerState.pid -ErrorAction SilentlyContinue
        if (!$worker) { continue }
        $started = if ($workerState.started_at -is [DateTime]) { $workerState.started_at.ToUniversalTime() } else { [DateTimeOffset]::Parse($workerState.started_at).UtcDateTime }
        if ($worker.Path -eq $workerState.python -and [Math]::Abs(($worker.StartTime.ToUniversalTime() - $started).TotalSeconds) -lt 2) { Stop-Process -Id $worker.Id }
    }
}
Write-Output "Stopped AiCA PID $($process.Id). Data was retained."
