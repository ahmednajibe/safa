# chocolateyuninstall.ps1 — run the Inno uninstaller silently (M10d).
# Per-user install → the key lives in HKCU; HKLM is listed in case a
# machine-wide Inno flag was ever used. Removal NEVER deletes user data:
# the Inno uninstaller keeps the data folder by default (it only offers
# deletion interactively, and silent mode answers its default = keep).
$ErrorActionPreference = 'Stop'
$appId = '{{APP_GUID}}'   # release-info.toml [guid] online
$keys = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$appId`_is1",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$appId`_is1"
)
$uninstall = $null
foreach ($k in $keys) {
    if (Test-Path $k) { $uninstall = Get-ItemProperty $k; break }
}
if (-not $uninstall) {
    Write-Warning 'Safa uninstall entry not found — nothing to do.'
    return
}
$line = $uninstall.QuietUninstallString
if (-not $line) { $line = $uninstall.UninstallString }
# "<path>\unins000.exe" /SILENT — pull the executable out of the string.
if ($line -match '^"([^"]+)"') { $exe = $Matches[1] }
else { $exe = ($line -split '\s+')[0] }
& $exe /VERYSILENT /NORESTART /SUPPRESSMSGBOXES
if ($LASTEXITCODE -ne 0) { throw "Inno uninstaller failed ($LASTEXITCODE)" }
