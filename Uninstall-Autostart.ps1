$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Stop.ps1')
$task = Get-ScheduledTask -TaskName 'QuotaStarter-WorkCodex' -ErrorAction SilentlyContinue
$expected = Join-Path $PSScriptRoot 'supervisor.py'
if ($task -and @($task.Actions | Where-Object { $_.Arguments -and $_.Arguments.Contains($expected) }).Count -gt 0) {
    Unregister-ScheduledTask -TaskName 'QuotaStarter-WorkCodex' -Confirm:$false
}
Write-Output 'Autostart removed. Source code and local logs were preserved.'
