param(
    [string]$Python = (Join-Path $env:USERPROFILE 'anaconda3/python.exe'),
    [int]$DPort = 8013,
    [int]$WebPort = 5174,
    [string]$Data = (Join-Path $env:LOCALAPPDATA 'AiCA/executable-platform')
)
$ErrorActionPreference = 'Stop'
$pipelineRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../../../..')).Path
$pipelineLogs = Join-Path $Data 'logs'
New-Item -ItemType Directory -Force -Path $pipelineLogs | Out-Null
foreach ($port in @($DPort,$WebPort)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "Port $port is occupied" }
}
if (!(Test-Path -LiteralPath $Python)) { throw "Python not found: $Python" }
$dlls = Get-ChildItem (Join-Path (Split-Path $Python) 'pkgs/libsqlite-*/Library/bin/sqlite3.dll') -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match 'libsqlite-(\d+\.\d+\.\d+)-' -and [version]$Matches[1] -ge [version]'3.51.3' } |
    Sort-Object { [void]($_.FullName -match 'libsqlite-(\d+\.\d+\.\d+)-'); [version]$Matches[1] } -Descending
if ($dlls) { $env:AICA_TEST_SQLITE_DLL = $dlls[0].FullName }
$env:AICA_LOCAL_API_TOKEN = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:AICA_API_TOKENS = @{ local = $env:AICA_LOCAL_API_TOKEN } | ConvertTo-Json -Compress
$env:AICA_BACKEND_URL = "http://127.0.0.1:$DPort"
$env:PYTHONPATH = (@('apps/gateway/src','packages/schema/src','packages/mcp-adapters/src','packages/runtime/src') | ForEach-Object { Join-Path $pipelineRoot $_ }) -join ';'
$env:PYTHONIOENCODING = 'utf-8'
if (!$env:PREVIEW_PROVIDER) { $env:PREVIEW_PROVIDER = 'local_fallback' }
$pipelineBackend = Start-Process -FilePath $Python -ArgumentList @('apps/gateway/src/preview/scripts/run_d.py','--data-dir',('"' + $Data + '"'),'--port',$DPort) -WorkingDirectory $pipelineRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $pipelineLogs 'backend.log') -RedirectStandardError (Join-Path $pipelineLogs 'backend-error.log') -PassThru
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        $pipelineBackend.Refresh()
        if ($pipelineBackend.HasExited) { throw "Backend exited; see $pipelineLogs/backend-error.log" }
        if (Get-NetTCPConnection -State Listen -LocalPort $DPort -ErrorAction SilentlyContinue) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (!$ready) { throw 'Backend startup timed out' }
    $pipelineWeb = Start-Process -FilePath (Get-Command node.exe).Source -ArgumentList @('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',$WebPort,'--strictPort') -WorkingDirectory (Join-Path $pipelineRoot 'apps/web') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $pipelineLogs 'web.log') -RedirectStandardError (Join-Path $pipelineLogs 'web-error.log') -PassThru
    @{ backendPid=$pipelineBackend.Id; webPid=$pipelineWeb.Id; url="http://127.0.0.1:$WebPort/#/execute"; checkout=$pipelineRoot } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $pipelineLogs 'processes.json')
    Write-Output "AiCA Runtime: http://127.0.0.1:$WebPort/#/execute"
    Write-Output "Backend PID $($pipelineBackend.Id), Web PID $($pipelineWeb.Id)"
} catch { if (!$pipelineBackend.HasExited) { Stop-Process -Id $pipelineBackend.Id -Force }; throw }
finally { Remove-Item Env:AICA_LOCAL_API_TOKEN -ErrorAction SilentlyContinue; Remove-Item Env:AICA_API_TOKENS -ErrorAction SilentlyContinue }
