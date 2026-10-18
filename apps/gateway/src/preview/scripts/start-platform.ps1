param(
    [string]$Python = (Join-Path $env:USERPROFILE 'anaconda3/python.exe'),
    [int]$Port = 8088,
    [string]$DeploymentDirectory = (Join-Path $env:LOCALAPPDATA 'AiCA/bd-latest')
)
$ErrorActionPreference = 'Stop'
$checkoutRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../../../..')).Path
$webDirectory = Join-Path $checkoutRoot 'apps/web/dist'
if (!(Test-Path -LiteralPath (Join-Path $webDirectory 'index.html'))) { throw 'Build apps/web first.' }
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { throw "Port $Port is occupied" }
if (!(Test-Path -LiteralPath $Python)) { throw 'Select an existing Python interpreter.' }
New-Item -ItemType Directory -Force -Path $DeploymentDirectory | Out-Null
$sources = Join-Path $DeploymentDirectory 'execution-sources-v1'
& $Python (Join-Path $PSScriptRoot 'prepare_runtime.py') --destination $sources
if ($LASTEXITCODE -ne 0) { throw 'Execution source preparation failed.' }
$names = @('AICA_DATA_DIR','AICA_API_TOKENS','AICA_WEB_DIR','AICA_CORS_ORIGINS','AICA_EXECUTION_SOURCES','AICA_TEST_SQLITE_DLL','PYTHONPATH','PYTHONIOENCODING')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $dlls = Get-ChildItem (Join-Path (Split-Path $Python) 'pkgs/libsqlite-*/Library/bin/sqlite3.dll') -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match 'libsqlite-(\d+\.\d+\.\d+)-' -and [version]$Matches[1] -ge [version]'3.51.3' } |
        Sort-Object { [void]($_.FullName -match 'libsqlite-(\d+\.\d+\.\d+)-'); [version]$Matches[1] } -Descending
    if ($dlls) { $env:AICA_TEST_SQLITE_DLL = $dlls[0].FullName }
    $env:AICA_DATA_DIR = Join-Path $DeploymentDirectory 'data'
    $env:AICA_API_TOKENS = '{}'
    $env:AICA_WEB_DIR = $webDirectory
    $env:AICA_CORS_ORIGINS = '[]'
    $env:AICA_EXECUTION_SOURCES = $sources
    $env:PYTHONIOENCODING = 'utf-8'
    $modulePaths = @((Join-Path $PSScriptRoot 'python_bootstrap')) + @(@('apps/gateway/src','packages/runtime/src','packages/schema/src','packages/mcp-adapters/src') | ForEach-Object { Join-Path $checkoutRoot $_ })
    $env:PYTHONPATH = $modulePaths -join [IO.Path]::PathSeparator
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $stdout = Join-Path $DeploymentDirectory "gateway-$stamp.log"
    $stderr = Join-Path $DeploymentDirectory "gateway-$stamp-error.log"
    $child = Start-Process -FilePath $Python -ArgumentList @('-m','uvicorn','aica_gateway.app:create_app','--factory','--host','127.0.0.1','--port',$Port,'--workers','1') -WorkingDirectory $checkoutRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    @{ pid=$child.Id; started_at=$child.StartTime.ToUniversalTime().ToString('o'); root=$checkoutRoot; python=$Python; host='127.0.0.1'; port=$Port; data=$env:AICA_DATA_DIR; web=$webDirectory; stdout=$stdout; stderr=$stderr } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $DeploymentDirectory 'service.json') -Encoding utf8
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $child.Refresh()
        if ($child.HasExited) { throw "Gateway exited; inspect $stderr" }
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 1
            if ($health.status -eq 'success') { Write-Output "AiCA: http://127.0.0.1:$Port/"; return }
        } catch { Start-Sleep -Milliseconds 300 }
    }
    Stop-Process -Id $child.Id
    throw 'Gateway startup timed out.'
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
