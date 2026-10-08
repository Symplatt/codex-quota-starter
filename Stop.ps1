$ErrorActionPreference = 'Stop'
$taskName = 'QuotaStarter-WorkCodex'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) { Stop-ScheduledTask -TaskName $taskName }
# Verify command lines before stopping PIDs, which may have been reused.
foreach ($name in @('supervisor','service')) {
    $pidFile = Join-Path $PSScriptRoot "data\$name.pid"
    if (Test-Path -LiteralPath $pidFile) {
        $targetPid = [int](Get-Content -LiteralPath $pidFile)
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid" -ErrorAction SilentlyContinue
        $expected = if ($name -eq 'service') { Join-Path $PSScriptRoot 'app.py' } else { Join-Path $PSScriptRoot 'supervisor.py' }
        if ($process -and $process.CommandLine.Contains($expected)) {
            & taskkill.exe /PID $targetPid /T /F | Out-Null
        }
    }
}
Write-Output 'Stopped. Scheduled logon startup remains installed; run Uninstall-Autostart.ps1 to remove it.'
