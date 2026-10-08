$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Uninstall-Autostart.ps1')
foreach ($folder in @('Programs','Desktop')) {
    $shortcutPath = Join-Path ([Environment]::GetFolderPath($folder)) 'Quota Starter.lnk'
    if (Test-Path -LiteralPath $shortcutPath) {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        if ($shortcut.TargetPath -eq (Join-Path $PSScriptRoot 'Start.cmd')) {
            Remove-Item -LiteralPath $shortcutPath
        }
    }
}
Write-Output 'Uninstalled background startup, desktop shortcut and Start menu entry. Program files and data remain for inspection; remove the installation folder manually if no longer needed.'
