# scripts/install-runtime.ps1 — provision the Safa runtime at install
# time (M10c §9.1 online installer, also used by install.ps1).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install-runtime.ps1
#       -Variant cpu|cu124 -AppDir <dir> -DataDir <dir>
#
# Steps (all logged to <DataDir>\logs\install.log):
#   1. uv python install 3.11 -> <AppDir>\pyroot\cpython-3.11.*
#   2. move that tree to <AppDir>\python (the layout Safa.exe expects)
#   3. drop EXTERNALLY-MANAGED so `uv pip --system` may write site-packages
#   4. uv pip install -r requirements-<variant>.txt (frozen, hash-checked)
#      with the matching pytorch index.
#
# Everything downloads under <DataDir>\tmp — nothing goes to the system
# drive. Exit 0 on success, nonzero on failure (the installer keeps the
# log and shows it bilingually).
param(
    [ValidateSet("cpu", "cu124")]
    [string]$Variant = "cpu",
    [Parameter(Mandatory = $true)][string]$AppDir,
    [Parameter(Mandatory = $true)][string]$DataDir
)
$ErrorActionPreference = "Stop"

$LogDir = Join-Path $DataDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataDir "tmp") | Out-Null
$env:UV_CACHE_DIR = Join-Path $DataDir "tmp\uv-cache"
$env:TEMP = Join-Path $DataDir "tmp"
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:PYTHONUTF8 = "1"

$Log = Join-Path $LogDir "install.log"
Start-Transcript -Path $Log -Force | Out-Null
try {
    $Uv = Join-Path $AppDir "tools\uv\uv.exe"
    if (-not (Test-Path $Uv)) { throw "uv.exe missing at $Uv" }
    $Req = Join-Path $AppDir "requirements-$Variant.txt"
    if (-not (Test-Path $Req)) { throw "requirements missing: $Req" }

    Write-Host "== Safa runtime install — variant $Variant =="
    Write-Host "AppDir : $AppDir"
    Write-Host "DataDir: $DataDir"

    Write-Host "== uv python install 3.11 =="
    $PyRoot = Join-Path $AppDir "pyroot"
    & $Uv python install 3.11 --install-dir $PyRoot --cache-dir $env:UV_CACHE_DIR
    if ($LASTEXITCODE -ne 0) { throw "uv python install failed ($LASTEXITCODE)" }

    $PySrc = Get-ChildItem -Path $PyRoot -Directory |
        Where-Object { $_.Name -match "^cpython-3\.11\.\d+-" } |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $PySrc) { throw "no cpython-3.11 install under $PyRoot" }

    $PyDir = Join-Path $AppDir "python"
    if (Test-Path $PyDir) { Remove-Item -Recurse -Force $PyDir }
    New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
    & robocopy $PySrc.FullName $PyDir /E /MOVE /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "robocopy python tree failed ($LASTEXITCODE)" }
    Remove-Item -Recurse -Force $PyRoot -ErrorAction SilentlyContinue

    $Py = Join-Path $PyDir "python.exe"
    Remove-Item -Force (Join-Path $PyDir "Lib\EXTERNALLY-MANAGED") `
        -ErrorAction SilentlyContinue
    & $Py -c "import sys; print('python', sys.version)" 
    if ($LASTEXITCODE -ne 0) { throw "bundled python failed to start" }

    Write-Host "== uv pip install ($Variant) =="
    # [tool.uv.sources] is project-only; name the pytorch index here so the
    # +cpu / +cu124 wheels resolve. unsafe-best-match keeps real PyPI first.
    & $Uv pip install `
        --python $Py --system `
        -r $Req `
        --require-hashes `
        --extra-index-url "https://download.pytorch.org/whl/$Variant" `
        --index-strategy unsafe-best-match `
        --cache-dir $env:UV_CACHE_DIR
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed ($LASTEXITCODE)" }

    Write-Host "== sanity check =="
    & $Py -W ignore -c "import fastapi, uvicorn, torch, onnxruntime, faster_whisper, audio_separator, silero_vad, df, pedalboard; print('ok')"
    if ($LASTEXITCODE -ne 0) { throw "import sanity check failed" }
    Write-Host "== done =="
    exit 0
} catch {
    Write-Host "INSTALL FAILED: $_"
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
