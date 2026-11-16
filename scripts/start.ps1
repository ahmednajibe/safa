# scripts/start.ps1 — Safa launcher shim (M10c).
# All launcher logic now lives in backend/launcher.py (cross-platform);
# this script only picks a Python interpreter and forwards args:
#   bundled python\python.exe (portable/installed) → .venv → uv run.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
#
#   -Port N           override SOUND_SYSTEM_PORT (validated 1024–65535)
#   -CheckOnly        print STATE:... and exit after port detection
#   -NoBrowser        don't open the browser
# PS 5.1 compatible; works with Arabic + spaces in the root path and no
# admin rights.
param(
    [int]$Port = 0,
    [switch]$CheckOnly,
    [switch]$NoBrowser,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# chcp 65001 equivalent + UTF-8 Python — Arabic filenames/paths must
# survive every layer.
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Fwd = @()
if ($Port -gt 0) { $Fwd += @("--port", "$Port") }
if ($CheckOnly) { $Fwd += "--check-only" }
if ($NoBrowser) { $Fwd += "--no-browser" }
if ($Rest) { $Fwd += $Rest }

$BundledPy = Join-Path $Root "python\python.exe"
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"

if (Test-Path $BundledPy) {
    & $BundledPy -m backend.launcher @Fwd
} elseif (Test-Path $VenvPy) {
    & $VenvPy -m backend.launcher @Fwd
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
    # First run on a dev checkout: `uv run` creates + syncs .venv itself.
    $extra = if ($env:SAFA_UV_EXTRA) { $env:SAFA_UV_EXTRA } else { "cu124" }
    & uv run --extra $extra python -m backend.launcher @Fwd
} else {
    Write-Host "No Python runtime found and 'uv' is not installed."
    Write-Host "لم يُعثر على بايثون و«uv» غير مثبت."
    exit 5
}
exit $LASTEXITCODE
