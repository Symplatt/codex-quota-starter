$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Stop.ps1')
if (Get-ScheduledTask -TaskName 'QuotaStarter-WorkCodex' -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName 'QuotaStarter-WorkCodex' -Confirm:$false
}
Write-Output 'Autostart removed. Source code and local logs were preserved.'
