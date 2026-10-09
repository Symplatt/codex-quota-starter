param([string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs\QuotaStarter'), [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$source = [IO.Path]::GetFullPath($PSScriptRoot)
$destination = [IO.Path]::GetFullPath($InstallDirectory)
if ($source -eq $destination) { throw 'Run Install.ps1 from the extracted release folder, not the installed folder.' }
$python = (Get-Command python -ErrorAction Stop).Source
& $python -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) { throw 'Install official Codex CLI first: npm install -g @openai/codex' }
$oldTask = Get-ScheduledTask -TaskName 'QuotaStarter-WorkCodex' -ErrorAction SilentlyContinue
$preserveDisabled = $oldTask -and (-not $oldTask.Settings.Enabled)
if (Test-Path -LiteralPath (Join-Path $destination 'Stop.ps1')) { & (Join-Path $destination 'Stop.ps1') }
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$files = @('windows_settings.py','Autostart-Control.ps1','app.py','core.py','rpc.py','supervisor.py','diagnose.py','ui.html','Start.ps1','Start.cmd','Stop.ps1','Install-Autostart.ps1','Install-Autostart.cmd','Uninstall-Autostart.ps1','Uninstall.ps1','README.md','MECHANISM.md','VALIDATION.md','CHANGELOG.md','requirements.txt','LICENSE')
$files = @('AGENTS.md','help.html','version.py') + $files
foreach ($file in $files) {
    $from = Join-Path $source $file
    if (-not (Test-Path -LiteralPath $from)) { throw "Missing release file: $file" }
}
foreach ($file in $files) { Copy-Item -LiteralPath (Join-Path $source $file) -Destination (Join-Path $destination $file) -Force }
# If installing from a locally tested source, preserve its deduplication ledger.
# Release archives never contain data. Existing installation data is never overwritten.
$fromDb = Join-Path $source 'data\quota.sqlite3'
$toDb = Join-Path $destination 'data\quota.sqlite3'
if ((Test-Path -LiteralPath $fromDb) -and -not (Test-Path -LiteralPath $toDb)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $toDb) | Out-Null
    & $python -c 'import sqlite3,sys; a=sqlite3.connect(sys.argv[1]); b=sqlite3.connect(sys.argv[2]); a.backup(b); b.close(); a.close()' $fromDb $toDb
    if ($LASTEXITCODE -ne 0) { throw 'Could not migrate local deduplication ledger.' }
}
& (Join-Path $destination 'Install-Autostart.ps1')
if ($preserveDisabled) { & (Join-Path $destination 'Autostart-Control.ps1') -Action disable | Out-Null }
$programs = [Environment]::GetFolderPath('Programs')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $programs 'Quota Starter.lnk'))
$shortcut.TargetPath = Join-Path $destination 'Start.cmd'
$shortcut.WorkingDirectory = $destination
$shortcut.Description = 'Work / Codex quota dashboard'
$shortcut.WindowStyle = 7
$shortcut.Save()
$desktop = [Environment]::GetFolderPath('Desktop')
$desktopShortcut = $shell.CreateShortcut((Join-Path $desktop 'Quota Starter.lnk'))
$desktopShortcut.TargetPath = Join-Path $destination 'Start.cmd'
$desktopShortcut.WorkingDirectory = $destination
$desktopShortcut.Description = 'Work / Codex quota dashboard'
$desktopShortcut.WindowStyle = 7
$desktopShortcut.Save()
& (Join-Path $destination 'Start.ps1') -NoBrowser:$NoBrowser
Write-Output "Installed: $destination"
