param([int]$Port = 8000, [string]$LocalAddress, [string]$RemoteSubnet)
$ErrorActionPreference = 'Stop'
if (-not $LocalAddress -or -not $RemoteSubnet) { throw 'Specify the verified LAN IPv4 address and CIDR subnet.' }
$ruleName = "AiCA-LAN-TCP-$Port"
$existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
if ($existing) { throw "Rule $ruleName already exists; inspect it before modifying." }
New-NetFirewallRule -Name $ruleName -DisplayName "AiCA LAN TCP $Port" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -LocalAddress $LocalAddress -RemoteAddress $RemoteSubnet -Profile Private -EdgeTraversalPolicy Block | Out-Null
Get-NetFirewallRule -Name $ruleName | Select-Object Name,Enabled,Direction,Action,Profile
