param(
    [string]$Python = (Join-Path $env:USERPROFILE 'anaconda3/python.exe'),
    [int]$RuntimePort = 8014,
    [int]$ParserPort = 8025,
    [int]$WebPort = 5176,
    [string]$Data = (Join-Path $env:LOCALAPPDATA 'AiCA/provider-completion'),
    [string]$ModelRelayHost = ''
)
$ErrorActionPreference = 'Stop'
$checkoutRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../../../..')).Path
$logDirectory = Join-Path $Data 'logs'
$runtimeSources = Join-Path $Data 'sources/runtime-6b98aa4-providers-v3'
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
foreach ($port in @($RuntimePort,$ParserPort,$WebPort)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "Port $port is occupied" }
}
& $Python (Join-Path $PSScriptRoot 'prepare_runtime.py') --destination $runtimeSources
if ($LASTEXITCODE -ne 0) { throw 'Runtime source preparation failed' }
$dlls = Get-ChildItem (Join-Path (Split-Path $Python) 'pkgs/libsqlite-*/Library/bin/sqlite3.dll') -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match 'libsqlite-(\d+\.\d+\.\d+)-' -and [version]$Matches[1] -ge [version]'3.51.3' } |
    Sort-Object { [void]($_.FullName -match 'libsqlite-(\d+\.\d+\.\d+)-'); [version]$Matches[1] } -Descending
if ($dlls) { $env:AICA_TEST_SQLITE_DLL = $dlls[0].FullName }
$env:AICA_LOCAL_API_TOKEN = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:AICA_API_TOKENS = @{ local = $env:AICA_LOCAL_API_TOKEN } | ConvertTo-Json -Compress
$env:AICA_BACKEND_URL = "http://127.0.0.1:$RuntimePort"
$env:AICA_PARSER_URL = "http://127.0.0.1:$ParserPort"
$env:PYTHONIOENCODING = 'utf-8'
if (!$env:PREVIEW_PROVIDER) { $env:PREVIEW_PROVIDER = 'prewarmed_cache' }
$children = @()
try {
    $relay = $null
    if ($ModelRelayHost) {
        $relay = Start-Process -FilePath $Python -ArgumentList @((Join-Path $PSScriptRoot 'model_relay.py'),'--host',$ModelRelayHost) -WorkingDirectory $checkoutRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'relay.log') -RedirectStandardError (Join-Path $logDirectory 'relay-error.log') -PassThru
        $children += $relay
    }
    foreach ($service in @('runtime','parser')) {
        $sources = if ($service -eq 'runtime') { $runtimeSources } else { $checkoutRoot }
        $port = if ($service -eq 'runtime') { $RuntimePort } else { $ParserPort }
        $modulePaths = @((Join-Path $PSScriptRoot 'python_bootstrap')) + @(@('apps/gateway/src','packages/schema/src','packages/mcp-adapters/src','packages/runtime/src') | ForEach-Object { Join-Path $sources $_ }) + @((Join-Path $checkoutRoot 'apps/gateway/src'))
        $env:PYTHONPATH = $modulePaths -join ';'
        $child = Start-Process -FilePath $Python -ArgumentList @((Join-Path $PSScriptRoot 'run_unified.py'),'--service',$service,'--data-dir',('"' + (Join-Path $Data $service) + '"'),'--port',$port) -WorkingDirectory $checkoutRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "$service.log") -RedirectStandardError (Join-Path $logDirectory "$service-error.log") -PassThru
        $children += $child
        if ($service -eq 'runtime') { $runtimeProcess = $child } else { $parserProcess = $child }
        $ready = $false
        for ($attempt = 0; $attempt -lt 90; $attempt++) {
            $child.Refresh()
            if ($child.HasExited) { throw "$service exited; see $logDirectory/$service-error.log" }
            if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { $ready = $true; break }
            Start-Sleep -Milliseconds 500
        }
        if (!$ready) { throw "$service startup timed out" }
    }
    $web = Start-Process -FilePath (Get-Command node.exe).Source -ArgumentList @('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',$WebPort,'--strictPort') -WorkingDirectory (Join-Path $checkoutRoot 'apps/web') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory 'web.log') -RedirectStandardError (Join-Path $logDirectory 'web-error.log') -PassThru
    $children += $web
    @{ runtimePid=$runtimeProcess.Id; parserPid=$parserProcess.Id; webPid=$web.Id; relayPid=if ($relay) { $relay.Id } else { $null }; url="http://127.0.0.1:$WebPort/#/execute"; checkout=$checkoutRoot } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $logDirectory 'processes.json')
    Write-Output "AiCA: http://127.0.0.1:$WebPort/#/execute"
} catch {
    foreach ($child in $children) { $child.Refresh(); if (!$child.HasExited) { Stop-Process -Id $child.Id -Force } }
    throw
} finally {
    Remove-Item Env:AICA_LOCAL_API_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:AICA_API_TOKENS -ErrorAction SilentlyContinue
}
