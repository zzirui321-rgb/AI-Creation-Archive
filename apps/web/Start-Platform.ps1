param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$gatewayScript = Join-Path $PSScriptRoot '../gateway/deployment/Start-AiCA.ps1'
& $gatewayScript -Port $Port
