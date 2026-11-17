# chocolateyinstall.ps1 — wrap the Safa online setup.exe (M10d).
# Rendered by render_choco.py; the committed template keeps {{...}} tokens.
#
# choco install safa --params "/Variant:gpu /DataDir:D:\SafaData /InstallDir:D:\Apps\Safa"
#   /Variant   cpu|gpu|auto → Inno /VARIANT= (default: auto — installer
#              auto-detects NVIDIA driver >= 551)
#   /DataDir   → Inno /DATADIR= (jobs/outputs/models; default %LOCALAPPDATA%\Safa)
#   /InstallDir→ Inno /DIR= (default %LOCALAPPDATA%\Programs\Safa)
#
# The download is verified against the release sha256 before running —
# same integrity contract as install.ps1. The installer runs per-user
# (/CURRENTUSER); no admin, no firewall rule, 127.0.0.1 only.
$ErrorActionPreference = 'Stop'
$packageName = 'safa'

$pp = @{}
if ($env:ChocolateyPackageParameters) {
    foreach ($m in [regex]::Matches($env:ChocolateyPackageParameters,
                                   '/([A-Za-z]+):("[^"]*"|[^\s]+)')) {
        $pp[$m.Groups[1].Value.ToLower()] = $m.Groups[2].Value.Trim('"')
    }
}
$variant = 'auto'
if ($pp.ContainsKey('variant')) { $variant = $pp['variant'] }
if ($variant -notin @('cpu', 'gpu', 'auto')) {
    throw "unknown /Variant: $variant (use cpu, gpu or auto)"
}

$silentArgs = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER' +
              " /VARIANT=$variant"
if ($pp.ContainsKey('datadir')) {
    $silentArgs += ' /DATADIR="' + $pp['datadir'] + '"'
}
if ($pp.ContainsKey('installdir')) {
    $silentArgs += ' /DIR="' + $pp['installdir'] + '"'
}
# keep the installer log inside the package's own folder, never %TEMP%
$logDir = Join-Path $env:LOCALAPPDATA 'Safa-install-logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$silentArgs += ' /LOG="' + (Join-Path $logDir 'safa-setup.log') + '"'

$packageArgs = @{
    packageName   = $packageName
    fileType      = 'exe'
    url           = '{{REPO}}/releases/download/v{{VERSION}}/{{SETUP_ASSET}}'
    checksum      = '{{SHA256}}'
    checksumType  = 'sha256'
    silentArgs    = $silentArgs
    validExitCodes = @(0)
}
Install-ChocolateyPackage @packageArgs
