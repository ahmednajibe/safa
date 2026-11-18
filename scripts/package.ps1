# scripts/package.ps1 - build the portable "share with a friend" package.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\package.ps1 -Variant gpu
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\package.ps1 -Variant cpu [-Full] [-StageOnly]
#
# Produces dist_pkg\Safa-<version>-win-x64-<variant>-portable[-full]\ plus
# the publishable artifact(s): a single .zip for cpu, split .7z volumes +
# SHA256SUMS + assemble-gpu.bat for gpu (M10c §9.4 — GitHub assets must
# stay < 2 GiB). -StageOnly leaves the folder and skips archiving (used
# by build-installer.ps1). The folder is fully portable: private Python
# 3.11 (uv-managed standalone build) with all wheels installed, prebuilt
# frontend, ffmpeg, Safa.exe launcher, READMEs, portable.txt marker.
# -Full additionally bundles models\.
#
# PS 5.1 compatible. Nothing outside F:\sound_system is written; every
# temp/cache stays under data\tmp. The dev .venv is never touched.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("gpu", "cpu")]
    [string]$Variant,
    [switch]$Full,
    [switch]$StageOnly
)
$ErrorActionPreference = "Stop"
$sw = [Diagnostics.Stopwatch]::StartNew()

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
. (Join-Path $PSScriptRoot "launcher-lib.ps1")

# Keep C: clean - all build caches/temp live under data\tmp.
$TmpRoot = Join-Path $Root "data\tmp\pkgbuild"
New-Item -ItemType Directory -Force -Path $TmpRoot | Out-Null
$env:UV_CACHE_DIR = Join-Path $Root "data\tmp\uv-cache"
$env:TEMP = $TmpRoot
$env:TMP = $TmpRoot
$env:TMPDIR = $TmpRoot
$env:PYTHONUTF8 = "1"

$Version = (Select-String -Path (Join-Path $Root "pyproject.toml") `
    -Pattern '^version = "([^"]+)"').Matches.Groups[1].Value
if (-not $Version) { throw "version not found in pyproject.toml" }

# Canonical artifact names come from release-info.toml via
# backend.core.release_info (single source of truth, M10c §7).
$AssetKey = if ($Variant -eq "gpu") { "gpu_portable_7z" } else { "cpu_portable_zip" }
$AssetName = (& uv run --no-sync python -m backend.core.release_info $AssetKey nnn=NNN 2>$null |
    Select-Object -First 1).Trim()
if (-not $AssetName) { throw "release_info $AssetKey failed" }
$AssetName = $AssetName -replace '\.NNN$', ''

$Name = "Safa-$Version-win-x64-$Variant-portable"
if ($Full) { $Name += "-full" }
$Stage = Join-Path $TmpRoot $Name
$DistDir = Join-Path $Root "dist_pkg"
$FinalDir = Join-Path $DistDir $Name
$ZipPath = Join-Path $DistDir $AssetName
$PartsDir = Join-Path $DistDir "gpu-portable"   # gpu release assets land flat here
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

Write-Host "== package $Name =="

# PS 5.1 + $ErrorActionPreference=Stop turns ANY native stderr line into a
# terminating error (harmless warnings like pkg_resources' kill the build).
# Run native commands under Continue and check $LASTEXITCODE instead.
function Invoke-Native([scriptblock]$Cmd, [string]$What) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Cmd
        if ($LASTEXITCODE -ne 0) { throw "$What failed ($LASTEXITCODE)" }
    } finally {
        $ErrorActionPreference = $prev
    }
}

# stdout of a bundled-python probe (stderr discarded): last line only.
function Get-PyProbe([string]$Code) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $StagePy -W ignore -c $Code 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return ("$out" -split "`n" | Select-Object -Last 1).Trim()
    } finally {
        $ErrorActionPreference = $prev
    }
}

# Defender/indexers briefly hold DLLs in fresh trees — retry deletes.
function Remove-WithRetry([string]$Path) {
    for ($i = 0; $i -lt 10 -and (Test-Path $Path); $i++) {
        try {
            Remove-Item -Recurse -Force $Path -ErrorAction Stop
        } catch {
            Start-Sleep -Seconds 3
        }
    }
    if (Test-Path $Path) { throw "could not remove $Path (file in use)" }
}

# ---- fresh frontend ------------------------------------------------------------
if (Test-StaleBuild -Root $Root) {
    Write-Host "== frontend build (stale) =="
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "frontend/dist is stale and npm is not available to rebuild it"
    }
    Push-Location (Join-Path $Root "frontend")
    try {
        Invoke-Native { npm run build } "npm run build"
    } finally {
        Pop-Location
    }
} else {
    Write-Host "== frontend/dist is fresh =="
}

# ---- private Python 3.11 --------------------------------------------------------
Write-Host "== standalone python =="
$PyRoot = Join-Path $TmpRoot "pyroot"
Invoke-Native {
    uv python install 3.11 --install-dir $PyRoot --cache-dir $env:UV_CACHE_DIR
} "uv python install"
$PySrc = Get-ChildItem -Path $PyRoot -Directory |
    Where-Object { $_.Name -match "^cpython-3\.11\.\d+-" } |
    Sort-Object Name -Descending | Select-Object -First 1
if (-not $PySrc) { throw "no cpython-3.11 install under $PyRoot" }
Write-Host "   using $($PySrc.Name)"

if (Test-Path $Stage) { Remove-WithRetry $Stage }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Copy-Item -Path $PySrc.FullName -Destination (Join-Path $Stage "python") -Recurse
$StagePy = Join-Path $Stage "python\python.exe"
# The copied tree is ours now, not uv's - let `uv pip install` target it.
Remove-Item -Force (Join-Path $Stage "python\Lib\EXTERNALLY-MANAGED") `
    -ErrorAction SilentlyContinue
if ((Get-PyProbe "import sys; print(sys.version_info[0])") -ne "3") {
    throw "bundled python failed to start"
}

# ---- dependencies ---------------------------------------------------------------
Write-Host "== install wheels ($Variant) =="
$Req = Join-Path $TmpRoot "req-$Variant.txt"
# One uv.lock now covers both variants via the conflicting `cpu`/`cu124`
# extras (M10c §2) — `packaging/cpu` is gone.
$Extra = if ($Variant -eq "gpu") { "cu124" } else { "cpu" }
Invoke-Native {
    uv run --no-project python (Join-Path $Root "scripts\export_requirements.py") `
        --extra $Extra --no-hashes -o $Req
} "export requirements ($Variant) - run 'uv lock' first"
$pipArgs = @(
    "pip", "install",
    "--no-config",  # pyproject's [tool.uv] override would drop onnxruntime
    "--no-deps",    # the export is the full locked closure - never re-resolve
    "--python", $StagePy,
    "--system",
    "--cache-dir", $env:UV_CACHE_DIR
)
# +cpu / +cu124 wheels only live on the pytorch indexes. [tool.uv.sources]
# does not apply to `uv pip` (project-only), so name the index here;
# unsafe-best-match keeps PyPI packages (e.g. certifi) resolving from
# PyPI instead of the stale copies on the pytorch index.
$pipArgs += @(
    "--extra-index-url", "https://download.pytorch.org/whl/$Extra",
    "--index-strategy", "unsafe-best-match"
)
$pipArgs += @("-r", $Req)
Invoke-Native { uv @pipArgs } "uv pip install"

# Sanity: the variant's CUDA story must be exactly what we claim.
$importsOK = Get-PyProbe `
    "import fastapi, uvicorn, torch, onnxruntime, faster_whisper, audio_separator, silero_vad, df, pedalboard, ctranslate2; print('ok')"
if ($importsOK -ne "ok") { throw "bundled env: import sanity check failed" }
$cudaVer = Get-PyProbe "import torch; print(torch.version.cuda)"
$SitePkgs = Join-Path $Stage "python\Lib\site-packages"
if ($Variant -eq "gpu") {
    if ($cudaVer -ne "12.4") { throw "gpu variant: torch.version.cuda=$cudaVer, expected 12.4" }
    # CUDA DLLs come from torch\lib on Windows (backend/core/gpu.py); the
    # ~2.7 GB nvidia-* wheels are Linux-only now — their return is a size bug.
    foreach ($dll in @("torch_cuda.dll", "cudnn64_9.dll", "cublas64_12.dll")) {
        if (-not (Test-Path (Join-Path $SitePkgs "torch\lib\$dll"))) {
            throw "gpu variant: torch\lib\$dll missing"
        }
    }
    if (Test-Path (Join-Path $SitePkgs "nvidia")) {
        throw "gpu variant: site-packages\nvidia present - duplicate CUDA wheels are back (pyproject markers?)"
    }
} else {
    if ($cudaVer -ne "None") { throw "cpu variant: torch.version.cuda=$cudaVer, expected None" }
    if (Test-Path (Join-Path $SitePkgs "nvidia")) {
        throw "cpu variant: unexpected site-packages\nvidia"
    }
}
Write-Host "   torch.cuda=$cudaVer"

# ---- app files -------------------------------------------------------------------
Write-Host "== copy app files =="
function Copy-Tree([string]$Src, [string]$Dst) {
    & robocopy $Src $Dst /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "robocopy $Src -> $Dst failed ($LASTEXITCODE)" }
}
Copy-Tree (Join-Path $Root "backend") (Join-Path $Stage "backend")
Copy-Tree (Join-Path $Root "frontend\dist") (Join-Path $Stage "frontend\dist")
Copy-Tree (Join-Path $Root "tools\ffmpeg") (Join-Path $Stage "tools\ffmpeg")

$ScriptsDir = Join-Path $Stage "scripts"
New-Item -ItemType Directory -Force -Path $ScriptsDir | Out-Null
foreach ($f in @("start.ps1", "launcher-lib.ps1", "prefetch_models.py")) {
    Copy-Item (Join-Path $Root "scripts\$f") (Join-Path $ScriptsDir $f)
}
foreach ($f in @("start.bat", "start.sh", "start.command",
                 "README.md", "README.ar.md", "LICENSE", "THIRD_PARTY_NOTICES.txt")) {
    $src = Join-Path $Root $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $Stage $f) }
}

# Native launcher + portable marker (M10c): Safa.exe wraps
# `python\python.exe -m backend.launcher` in a kill-on-close Job Object;
# portable.txt tells paths.py this tree is self-contained. Safa.exe is a
# gitignored build output — compile it when missing/stale.
if (Test-SafaLauncherStale (Join-Path $Root "packaging\windows\launcher")) {
    Write-Host "== compile Safa.exe launcher =="
    Invoke-Native {
        powershell -NoProfile -ExecutionPolicy Bypass -File `
            (Join-Path $PSScriptRoot "build-launcher.ps1")
    } "build-launcher.ps1"
}
Copy-Item (Join-Path $Root "packaging\windows\launcher\Safa.exe") `
    (Join-Path $Stage "Safa.exe")
Copy-Item (Join-Path $Root "packaging\windows\launcher\safa.ico") `
    (Join-Path $Stage "safa.ico")
"Safa portable package - data and models stay inside this folder." |
    Set-Content -Path (Join-Path $Stage "portable.txt") -Encoding ASCII

$LicDir = Join-Path $Stage "LICENSES"
New-Item -ItemType Directory -Force -Path $LicDir | Out-Null
@"
Bundled third-party tools - license notices
=============================================

ffmpeg 7.1.1 (essentials build, GyanD/codexffmpeg on GitHub)
    tools\ffmpeg\bin\{ffmpeg,ffplay,ffprobe}.exe
    License: GPL v3 / LGPL v2.1 depending on build options - see
    https://ffmpeg.org/legal.html and the source release notes.

Python 3.11 (python-build-standalone via uv)
    python\
    License: PSF - see python\LICENSE.txt in this package.

PyPI packages under python\Lib\site-packages carry their own
licenses in each <package>-<version>.dist-info folder.
"@ | Set-Content -Path (Join-Path $LicDir "third-party.txt") -Encoding UTF8

"Safa (صفا) $Version - portable $Variant package$(if ($Full) { ' (models bundled)' })
Built: $(Get-Date -Format 'yyyy-MM-dd HH:mm')
Start: double-click Safa.exe (or start.bat)" |
    Set-Content -Path (Join-Path $Stage "VERSION.txt") -Encoding UTF8

if ($Full) {
    Write-Host "== bundle models =="
    Copy-Tree (Join-Path $Root "models") (Join-Path $Stage "models")
}

# ---- move to dist_pkg + archive ------------------------------------------------------
if (Test-Path $FinalDir) { Remove-WithRetry $FinalDir }
Move-Item -Path $Stage -Destination $FinalDir

if (-not $StageOnly -and $Variant -eq "cpu") {
    if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
    Write-Host "== zip =="
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory(
        $FinalDir, $ZipPath,
        [IO.Compression.CompressionLevel]::Optimal, $true)
}

if (-not $StageOnly -and $Variant -eq "gpu") {
    # M10c §9.4: the ~5 GiB tree splits into <= 1.8 GiB 7z volumes so every
    # GitHub release asset stays under the 2 GiB cap. The user downloads
    # all parts + SHA256SUMS.txt + assemble-gpu.bat into one folder.
    $SevenZip = Join-Path $Root "tools\7zip\7z.exe"
    if (-not (Test-Path $SevenZip)) {
        throw "7z.exe not found at $SevenZip - see docs (7-Zip MSI extract)"
    }
    New-Item -ItemType Directory -Force -Path $PartsDir | Out-Null
    Get-ChildItem $PartsDir -File | Remove-Item -Force
    $Base = Join-Path $PartsDir $AssetName          # Safa-<v>-win-x64-gpu-portable.7z
    Write-Host "== 7z volumes (<= 1.8 GiB) =="
    Invoke-Native {
        & $SevenZip a -mx=5 "-v1800m" "$Base" "$FinalDir" | Out-Null
    } "7z split"
    # Every artifact gets a SHA256 line: 7z.<NNN> volumes only.
    $Sums = Join-Path $PartsDir ("$AssetName" -replace '\.7z$', '-SHA256SUMS.txt')
    $lines = Get-ChildItem $PartsDir -File -Filter "$([IO.Path]::GetFileName($AssetName)).*" |
        Sort-Object Name | ForEach-Object {
            "$((Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower())  $($_.Name)"
        }
    $lines | Set-Content -Path $Sums -Encoding ASCII
    foreach ($f in @("7z.exe", "7z.dll")) {
        Copy-Item (Join-Path $Root "tools\7zip\$f") (Join-Path $PartsDir $f)
    }
    Copy-Item (Join-Path $Root "packaging\windows\assemble-gpu.bat") `
        (Join-Path $PartsDir "assemble-gpu.bat")
    Write-Host "   parts -> $PartsDir"
}

# ---- report ------------------------------------------------------------------------
$folderBytes = (Get-ChildItem -Path $FinalDir -Recurse -File |
    Measure-Object Length -Sum).Sum
$report = [ordered]@{
    package      = $Name
    folder_GiB   = [math]::Round($folderBytes / 1GB, 2)
    build_min    = [math]::Round($sw.Elapsed.TotalMinutes, 1)
    folder       = $FinalDir
}
if (-not $StageOnly -and $Variant -eq "cpu") {
    $report["zip_GiB"] = [math]::Round((Get-Item $ZipPath).Length / 1GB, 2)
    $report["zip"] = $ZipPath
    $report["extract_headroom_note"] = "unzip needs ~$([math]::Round($folderBytes/1GB,1)) GiB free; keep ~1 GiB extra for models/data on first run"
}
if (-not $StageOnly -and $Variant -eq "gpu") {
    $report["parts_dir"] = $PartsDir
    $report["volumes"] = (Get-ChildItem $PartsDir -File -Filter "*.7z.*").Count
}
$sw.Stop()
[pscustomobject]$report | Format-List
