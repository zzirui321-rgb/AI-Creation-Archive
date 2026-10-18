param(
    [int]$Port = 8000,
    [string]$BindAddress = '0.0.0.0',
    [string]$DeploymentDirectory = (Join-Path $env:LOCALAPPDATA 'AiCA/lan-deployment')
)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$webDirectory = Join-Path $repoRoot 'apps/web/dist'
$executionSources = Join-Path $DeploymentDirectory 'execution-sources-v1'
if (-not (Test-Path -LiteralPath (Join-Path $webDirectory 'index.html'))) { throw 'Build apps/web before starting.' }
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { throw "Port $Port is already in use." }
$pythonResult = & conda run --no-capture-output -n aica python -c 'import sys; print(sys.executable)'
if ($LASTEXITCODE -ne 0) { throw 'Existing aica environment is unavailable.' }
$pythonExecutable = ($pythonResult | Select-Object -Last 1).Trim()
New-Item -ItemType Directory -Path $DeploymentDirectory -Force | Out-Null
$statePath = Join-Path $DeploymentDirectory 'service.json'
$logStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdoutPath = Join-Path $DeploymentDirectory "gateway-$logStamp.stdout.log"
$stderrPath = Join-Path $DeploymentDirectory "gateway-$logStamp.stderr.log"
$environmentNames = @('AICA_DATA_DIR','AICA_API_TOKENS','AICA_WEB_DIR','AICA_CORS_ORIGINS','PYTHONPATH','AICA_EXECUTION_SOURCES')
$previous = @{}
foreach ($name in $environmentNames) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:AICA_DATA_DIR = Join-Path $DeploymentDirectory 'data'
    $env:AICA_API_TOKENS = '{}'
    $env:AICA_WEB_DIR = $webDirectory
    $env:AICA_CORS_ORIGINS = '[]'
    if (Test-Path -LiteralPath $executionSources) { $env:AICA_EXECUTION_SOURCES = $executionSources }
    $env:PYTHONPATH = (@('apps/gateway/src','packages/runtime/src','packages/schema/src','packages/mcp-adapters/src') | ForEach-Object { Join-Path $repoRoot $_ }) -join [IO.Path]::PathSeparator
    $arguments = @('-m','uvicorn','aica_gateway.app:create_app','--factory','--host',$BindAddress,'--port',"$Port",'--workers','1','--log-level','info')
    $process = Start-Process -FilePath $pythonExecutable -ArgumentList $arguments -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    @{ pid=$process.Id; started_at=$process.StartTime.ToUniversalTime().ToString('o'); root=$repoRoot; python=$pythonExecutable; host=$BindAddress; port=$Port; data=$env:AICA_DATA_DIR; web=$webDirectory; stdout=$stdoutPath; stderr=$stderrPath } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    $healthHost = if ($BindAddress -eq '0.0.0.0') { '127.0.0.1' } else { $BindAddress }
    do {
        $process.Refresh()
        if ($process.HasExited) { throw "Gateway exited. Inspect $stderrPath" }
        try {
            $response = Invoke-RestMethod -Uri "http://${healthHost}:$Port/health" -TimeoutSec 2
            if ($response.status -eq 'success') { Write-Output "AiCA is running: http://127.0.0.1:$Port/ ; PID $($process.Id) ; state $statePath"; return }
        } catch { Start-Sleep -Milliseconds 200 }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Gateway did not become healthy. Inspect $stderrPath"
} finally {
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
