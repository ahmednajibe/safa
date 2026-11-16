# scripts/build-launcher.ps1 — compile Safa.exe (M10c).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-launcher.ps1 [-Force]
#
# Safa.exe wraps `python -m backend.launcher` in a kill-on-close Job
# Object. It is a compiled artifact — gitignored, built here with the
# .NET Framework csc.exe (no NuGet, no SDK needed). Called by
# package.ps1 / build-installer.ps1 when Test-SafaLauncherStale fires,
# and by .github/workflows/release.yml.
param([switch]$Force)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Src = Join-Path $Root "packaging\windows\launcher"
$Exe = Join-Path $Src "Safa.exe"
$Cs = Join-Path $Src "Safa.cs"
$Ico = Join-Path $Src "safa.ico"

if (-not $Force) {
    . (Join-Path $PSScriptRoot "launcher-lib.ps1")
    if (-not (Test-SafaLauncherStale $Src)) {
        Write-Host "Safa.exe is fresh - skipping"
        return
    }
}

$Csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $Csc)) { throw "csc.exe not found at $Csc" }

Write-Host "== csc Safa.cs -> Safa.exe =="
& $Csc /nologo /target:winexe /platform:x64 /optimize+ `
    "/win32icon:$Ico" `
    "/out:$Exe" `
    $Cs
if ($LASTEXITCODE -ne 0) { throw "csc failed ($LASTEXITCODE)" }
if (-not (Test-Path $Exe)) { throw "csc produced no $Exe" }
Write-Host "   $Exe"
