param([string]$DeploymentDirectory = (Join-Path $env:LOCALAPPDATA 'AiCA/lan-deployment'))
$ErrorActionPreference = 'Stop'
$state = Get-Content (Join-Path $DeploymentDirectory 'https-service.json') -Raw | ConvertFrom-Json
$process = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
if (!$process) { return }
$recorded = if ($state.started_at -is [DateTime]) { $state.started_at.ToUniversalTime() } else { [DateTimeOffset]::Parse($state.started_at).UtcDateTime }
if ($process.Path -ne $state.python -or [Math]::Abs(($process.StartTime.ToUniversalTime() - $recorded).TotalSeconds) -gt 1) { throw 'HTTPS PID identity changed; refusing to stop.' }
Stop-Process -Id $process.Id
if (!$process.WaitForExit(10000)) { throw 'HTTPS proxy did not exit.' }
