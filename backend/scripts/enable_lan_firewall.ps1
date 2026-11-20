param(
    [string]$LocalAddress = '192.168.1.230',
    [string]$InterfaceAlias = 'WLAN',
    [int]$Port = 8870
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Please run this script as administrator.'
}
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Invalid port.' }
$address = Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -eq $LocalAddress }
if (-not $address) { throw 'LocalAddress is not assigned to this network interface.' }
$name = 'QwenImageClient-LAN-8870'
$options = @{
    Direction='Inbound'; Action='Allow'; Protocol='TCP'; LocalPort=$Port
    LocalAddress=$LocalAddress; RemoteAddress='LocalSubnet'
    InterfaceAlias=$InterfaceAlias; Profile='Any'; Enabled='True'
}
if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -Name $name @options | Out-Null
} else {
    New-NetFirewallRule -Name $name -DisplayName 'Qwen Image Client LAN 8870' @options | Out-Null
}
Write-Host "LAN firewall enabled: http://${LocalAddress}:${Port} (local subnet only)."
