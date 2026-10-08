param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$running = $false
try { $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8769/api/status' -TimeoutSec 2; $running = $status.service -eq 'quota-starter' } catch {}
if (-not $running) {
    $python = (Get-Command python -ErrorAction Stop).Source
    $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $pythonw)) { throw 'Python 3.11+ pythonw.exe is required.' }
    Start-Process -FilePath $pythonw -ArgumentList @('-X','utf8',('"' + (Join-Path $PSScriptRoot 'supervisor.py') + '"')) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
    for ($i=0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        try { $status=Invoke-RestMethod -Uri 'http://127.0.0.1:8769/api/status' -TimeoutSec 1; if ($status.service -eq 'quota-starter') { $running=$true; break } } catch {}
    }
    if (-not $running) { throw 'Service did not start. Check data\supervisor.log and port 8769.' }
}
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8769' }
Write-Output 'Quota Starter is running: http://127.0.0.1:8769'
