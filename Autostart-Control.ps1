param([ValidateSet('status','enable','disable')][string]$Action='status')
$ErrorActionPreference = 'Stop'
$taskName = 'QuotaStarter-WorkCodex'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$expected = Join-Path $PSScriptRoot 'supervisor.py'
if ($task -and @($task.Actions | Where-Object { $_.Arguments -and $_.Arguments.Contains($expected) }).Count -eq 0) {
    throw 'The startup task belongs to a different installation. Open that installation or reinstall this copy.'
}
if ($Action -eq 'enable') {
    if (-not $task) { & (Join-Path $PSScriptRoot 'Install-Autostart.ps1') | Out-Null }
    else { Enable-ScheduledTask -TaskName $taskName | Out-Null }
} elseif ($Action -eq 'disable' -and $task) {
    Disable-ScheduledTask -TaskName $taskName | Out-Null
}
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
[pscustomobject]@{available=$true; installed=($null -ne $task); enabled=($null -ne $task -and $task.Settings.Enabled); error=$null} | ConvertTo-Json -Compress
