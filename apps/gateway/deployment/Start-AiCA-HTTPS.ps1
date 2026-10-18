param(
    [Parameter(Mandatory=$true)][string]$LocalAddress,
    [int]$Port = 8443,
    [int]$UpstreamPort = 8000,
    [string]$DeploymentDirectory = (Join-Path $env:LOCALAPPDATA 'AiCA/lan-deployment')
)
$ErrorActionPreference = 'Stop'
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) { throw "Port $Port is already in use." }
$state = Get-Content (Join-Path $DeploymentDirectory 'service.json') -Raw | ConvertFrom-Json
$script = Join-Path $PSScriptRoot 'https_proxy.py'
$tls = Join-Path $DeploymentDirectory 'tls'
New-Item -ItemType Directory -Force -Path $tls | Out-Null
$arguments = @('"' + $script + '"','--certificates','"' + $tls + '"','--address',$LocalAddress,'--port',"$Port",'--upstream',"$UpstreamPort")
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$process = Start-Process -FilePath $state.python -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $tls "$stamp.stdout.log") -RedirectStandardError (Join-Path $tls "$stamp.stderr.log")
@{ pid=$process.Id; python=$state.python; started_at=$process.StartTime.ToUniversalTime().ToString('o'); port=$Port; url="https://${LocalAddress}:$Port"; ca=(Join-Path $tls 'ca.pem') } | ConvertTo-Json | Set-Content (Join-Path $DeploymentDirectory 'https-service.json') -Encoding utf8
Write-Output "HTTPS process $($process.Id) started; verify with tls/ca.pem. Trust is not installed automatically."
