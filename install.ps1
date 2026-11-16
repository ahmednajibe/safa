# install.ps1 — Safa one-line installer for Windows (M10c §9.9/13).
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#   .\install.ps1 -Variant gpu -InstallDir D:\Apps\Safa -DataDir D:\SafaData
#   .\install.ps1 -Offline -SetupExe .\Safa-0.1.0-win-x64-setup.exe
#   .\install.ps1 -DryRun
#
# Thin VERIFIED wrapper around the Inno setup executable. Downloads the
# release SHA256SUMS.txt + setup exe, verifies the SHA-256, then runs
# setup silently — all provisioning is done by the installer itself.
# This script NEVER deletes or edits anything inside InstallDir; Inno
# handles upgrades.
#
# "latest" resolves the release tag via the GitHub API so the versioned
# asset name can be built (release assets carry the version in their
# name, so `latest/download` alone is not enough). Pass -Version to skip
# that call.
[CmdletBinding()]
param(
    [ValidateSet('auto', 'cpu', 'gpu')]
    [string]$Variant = 'auto',
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\Safa'),
    [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'Safa'),
    [string]$Version = 'latest',
    [string]$Repo = 'https://github.com/<owner>/safa',
    [switch]$Offline,
    [string]$SetupExe = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is 50x slower otherwise

function Stop-Safa {
    param([string]$En, [string]$Ar, [int]$Code = 1)
    Write-Host "Safa: $En" -ForegroundColor Red
    Write-Host "صفا: $Ar" -ForegroundColor Red
    # Run as a file (-File / .\install.ps1): exit with a code. Run via
    # `irm … | iex` there is no script file, and `exit` would close the
    # user's whole PowerShell window — throw instead.
    if ($PSCommandPath) { exit $Code }
    throw "Safa install failed: $En"
}

function Test-SafaSha256 {
    param([Parameter(Mandatory)][string]$Path,
          [Parameter(Mandatory)][string]$Expected)
    $actual = (Get-FileHash -Algorithm SHA256 -Path $Path).Hash
    return ($actual.Trim().ToLowerInvariant() -eq
            $Expected.Trim().ToLowerInvariant())
}

function Get-SafaSha256Entry {
    # sha256sum format: "<hash>  <name>" (name may carry a * or ./ prefix)
    param([Parameter(Mandatory)][string]$SumsFile,
          [Parameter(Mandatory)][string]$Name)
    foreach ($line in Get-Content -LiteralPath $SumsFile -Encoding UTF8) {
        $line = $line.Trim()
        if (-not $line) { continue }
        $sp = $line.IndexOf(' ')
        if ($sp -lt 0) { continue }
        $hash = $line.Substring(0, $sp).Trim()
        $n = $line.Substring($sp).Trim().TrimStart('*')
        if ($n.StartsWith('./')) { $n = $n.Substring(2) }
        if ($n -eq $Name) { return $hash.ToLowerInvariant() }
    }
    return $null
}

function Resolve-SafaLatestVersion {
    param([Parameter(Mandatory)][string]$Repo)
    # Unfilled <owner> placeholder: never query the network with it.
    if ($Repo -match '<owner>') { return $null }
    $m = [regex]::Match($Repo, 'github\.com/([^/]+)/([^/]+?)/?$')
    if (-not $m.Success) { return $null }
    $api = 'https://api.github.com/repos/{0}/{1}/releases/latest' -f `
        $m.Groups[1].Value, $m.Groups[2].Value
    try {
        $r = Invoke-RestMethod -Uri $api -TimeoutSec 30 `
            -Headers @{ 'User-Agent' = 'safa-installer' }
        $tag = [string]$r.tag_name
        if ($tag -match '^v(.+)$') { return $Matches[1] }
        if ($tag) { return $tag }
    } catch { }
    return $null
}

function Get-SafaNoSpaceLogDir {
    # Inno exits 4 when /LOG path contains a space — pick the first
    # no-space base (LOCALAPPDATA may itself contain spaces via username).
    foreach ($base in @($env:LOCALAPPDATA, $env:ProgramData, $env:TEMP)) {
        if (-not $base) { continue }
        $d = Join-Path $base 'Safa-install-logs'
        if ($d -notmatch '\s') { return $d }
    }
    return $null
}

# ---- plan ----------------------------------------------------------------------
$setupName = $null
$setupPath = $null
$tmp = $null

if ($Offline) {
    if (-not $SetupExe) {
        Stop-Safa '-Offline requires -SetupExe <path to setup.exe>' `
                  'الوضع دون اتصال يتطلب -SetupExe' 2
    }
    if (-not (Test-Path -LiteralPath $SetupExe)) {
        Stop-Safa "setup exe not found: $SetupExe" `
                  "ملف التثبيت غير موجود: $SetupExe" 2
    }
    $setupPath = (Resolve-Path -LiteralPath $SetupExe).Path
    $setupName = Split-Path $setupPath -Leaf
    $source = $setupPath
} else {
    if ($Version -ne 'latest') {
        $setupName = "Safa-$Version-win-x64-setup.exe"
    } else {
        $setupName = 'Safa-<resolved>-win-x64-setup.exe'
    }
    $source = "$Repo/releases/download/v$Version/$setupName"
}

Write-Host '== Safa install =='
Write-Host "variant = $Variant"
Write-Host "dir     = $InstallDir"
Write-Host "data    = $DataDir"
Write-Host "setup   = $source"

if ($DryRun) {
    Write-Host '[dry-run] would: download + verify SHA256, then run'
    Write-Host "  $setupName /VERYSILENT /SUPPRESSMSGBOXES /NORESTART" `
        + ' /CURRENTUSER /VARIANT=' + $Variant
    Write-Host "  /DIR=`"$InstallDir`" /DATADIR=`"$DataDir`""
    Write-Host '  /LOG=<no-space temp log copied into DataDir>\logs\setup.log'
    exit 0
}

# ---- download ------------------------------------------------------------------
if (-not $Offline) {
    if ($Version -eq 'latest') {
        $Version = Resolve-SafaLatestVersion $Repo
        if (-not $Version) {
            Stop-Safa 'could not resolve the latest release tag — pass -Version' `
                      'تعذر معرفة أحدث إصدار — مرر -Version'
        }
    }
    $setupName = "Safa-$Version-win-x64-setup.exe"
    $base = "$Repo/releases/download/v$Version"
    $tmp = Join-Path $env:TEMP ('safa-setup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $setupPath = Join-Path $tmp $setupName

    Write-Host '== download =='
    $sumsPath = Join-Path $tmp 'SHA256SUMS.txt'
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "$base/SHA256SUMS.txt" `
            -OutFile $sumsPath
    } catch {
        Stop-Safa 'could not download SHA256SUMS.txt — refusing to install' `
                  'تعذر تنزيل ملف البصمة — أوقف التثبيت'
    }
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "$base/$setupName" `
            -OutFile $setupPath
    } catch {
        Stop-Safa "download failed: $base/$setupName" 'فشل التنزيل'
    }
}

# ---- verify --------------------------------------------------------------------
$sums = $null
if ($Offline) {
    $cand = Join-Path (Split-Path $setupPath -Parent) 'SHA256SUMS.txt'
    if (Test-Path -LiteralPath $cand) {
        $sums = $cand
    } else {
        Write-Host 'note: no SHA256SUMS.txt next to the setup exe — skipping verification'
    }
} else {
    $sums = Join-Path $tmp 'SHA256SUMS.txt'
}

if ($sums) {
    Write-Host '== verify =='
    $expected = Get-SafaSha256Entry -SumsFile $sums -Name $setupName
    if (-not $expected) {
        if (-not $Offline) {
            Remove-Item -LiteralPath $setupPath -Force -ErrorAction SilentlyContinue
        }
        Stop-Safa "no checksum entry for $setupName — refusing to install" `
                  'لا توجد بصمة للملف — أوقف التثبيت'
    }
    if (-not (Test-SafaSha256 -Path $setupPath -Expected $expected)) {
        if (-not $Offline) {
            Remove-Item -LiteralPath $setupPath -Force -ErrorAction SilentlyContinue
        }
        Stop-Safa "checksum mismatch for $setupName — refusing to install" `
                  'عدم تطابق البصمة — أوقف التثبيت'
    }
    Write-Host 'sha256 ok'
}

# ---- run setup -------------------------------------------------------------------
Write-Host '== setup =='
$logArgs = ''
$logDir = Get-SafaNoSpaceLogDir
$logFile = $null
if ($logDir) {
    New-Item -ItemType Directory -Force $logDir | Out-Null
    $logFile = Join-Path $logDir ('setup-{0:yyyyMMdd-HHmmss}.log' -f (Get-Date))
    $logArgs = " /LOG=`"$logFile`""
} else {
    Write-Host 'note: no space-free log dir found — setup runs without /LOG'
}

# ONE verbatim argument string via ProcessStartInfo — PowerShell's native
# arg marshalling strips the embedded quotes and Inno then truncates /DIR
# at the first space. Arabic + spacey paths must survive.
$setupArgs = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER ' +
    "/VARIANT=$Variant /DIR=`"$InstallDir`" /DATADIR=`"$DataDir`"" + $logArgs
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $setupPath
$psi.Arguments = $setupArgs
$psi.UseShellExecute = $false
$p = [System.Diagnostics.Process]::Start($psi)
$p.WaitForExit()
$rc = $p.ExitCode

if ($logFile) {
    New-Item -ItemType Directory -Force (Join-Path $DataDir 'logs') | Out-Null
    Copy-Item -LiteralPath $logFile -Destination `
        (Join-Path $DataDir 'logs\setup.log') -Force -ErrorAction SilentlyContinue
}
if ($rc -ne 0) {
    Stop-Safa "setup exited $rc — see $DataDir\logs\setup.log" `
              "فشل التثبيت — راجع السجل في $DataDir" $rc
}

if ($tmp) {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host '== done =='
Write-Host "Safa installed. Log: $DataDir\logs\setup.log"
