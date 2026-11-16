# scripts/package.ps1 - build the portable "share with a friend" package (M10b).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\package.ps1 -Variant gpu
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\package.ps1 -Variant cpu [-Full]
#
# Produces dist_pkg\SoundSystem-<version>-win64-<variant>[-full]\ and a
# matching .zip next to it. The folder is fully portable: private Python
# 3.11 (uv-managed standalone build) with all wheels installed, prebuilt
# frontend, ffmpeg, launcher, READMEs. -Full additionally bundles models\.
#
# PS 5.1 compatible. Nothing outside F:\sound_system is written; every
# temp/cache stays under data\tmp. The dev .venv is never touched.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("gpu", "cpu")]
    [string]$Variant,
    [switch]$Full
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

$Name = "SoundSystem-$Version-win64-$Variant"
if ($Full) { $Name += "-full" }
$Stage = Join-Path $TmpRoot $Name
$DistDir = Join-Path $Root "dist_pkg"
$FinalDir = Join-Path $DistDir $Name
$ZipPath = Join-Path $DistDir "$Name.zip"
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
if ($Variant -eq "gpu") {
    Invoke-Native {
        uv export --frozen --no-dev --no-hashes -o $Req
    } "uv export (gpu) - run 'uv lock' first"
} else {
    Invoke-Native {
        uv lock --project (Join-Path $Root "packaging\cpu")
    } "uv lock (cpu)"
    Invoke-Native {
        uv export --project (Join-Path $Root "packaging\cpu") `
            --frozen --no-dev --no-hashes -o $Req
    } "uv export (cpu)"
}
$pipArgs = @(
    "pip", "install",
    "--python", $StagePy,
    "--system",
    "--cache-dir", $env:UV_CACHE_DIR
)
if ($Variant -eq "cpu") {
    # Config-free: the root project's override (onnxruntime →
    # sys_platform=='never') must not leak into the CPU install — here the
    # real onnxruntime wheel is wanted.
    $pipArgs += "--no-config"
} else {
    # +cu124 wheels only live on the pytorch index. [tool.uv.sources] does
    # not apply to `uv pip` (project-only), so name the index here;
    # unsafe-best-match keeps PyPI packages (e.g. certifi) resolving from
    # PyPI instead of the stale copies on the pytorch index.
    $pipArgs += @(
        "--extra-index-url", "https://download.pytorch.org/whl/cu124",
        "--index-strategy", "unsafe-best-match"
    )
}
$pipArgs += @("-r", $Req)
# gpu installs WITH the project config (cwd = $Root): the
# override-dependencies entry keeps every onnxruntime requirement
# (direct or transitive) mapped to onnxruntime-gpu - like `uv sync`.
Invoke-Native { uv @pipArgs } "uv pip install"

# Sanity: the variant's CUDA story must be exactly what we claim.
$importsOK = Get-PyProbe `
    "import fastapi, uvicorn, torch, onnxruntime, faster_whisper, audio_separator, silero_vad, df, pedalboard, ctranslate2; print('ok')"
if ($importsOK -ne "ok") { throw "bundled env: import sanity check failed" }
$cudaVer = Get-PyProbe "import torch; print(torch.version.cuda)"
$SitePkgs = Join-Path $Stage "python\Lib\site-packages"
if ($Variant -eq "gpu") {
    if ($cudaVer -ne "12.4") { throw "gpu variant: torch.version.cuda=$cudaVer, expected 12.4" }
    if (-not (Test-Path (Join-Path $SitePkgs "nvidia"))) {
        throw "gpu variant: site-packages\nvidia missing"
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
foreach ($f in @("start.bat", "README.md", "README.ar.md")) {
    Copy-Item (Join-Path $Root $f) (Join-Path $Stage $f)
}

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

"Sound System $Version - portable $Variant package$(if ($Full) { ' (models bundled)' })
Built: $(Get-Date -Format 'yyyy-MM-dd HH:mm')
Start: double-click start.bat" |
    Set-Content -Path (Join-Path $Stage "VERSION.txt") -Encoding UTF8

if ($Full) {
    Write-Host "== bundle models =="
    Copy-Tree (Join-Path $Root "models") (Join-Path $Stage "models")
}

# ---- move to dist_pkg + zip --------------------------------------------------------
if (Test-Path $FinalDir) { Remove-WithRetry $FinalDir }
Move-Item -Path $Stage -Destination $FinalDir

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Write-Host "== zip =="
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory(
    $FinalDir, $ZipPath,
    [IO.Compression.CompressionLevel]::Optimal, $true)

# ---- report ------------------------------------------------------------------------
$folderBytes = (Get-ChildItem -Path $FinalDir -Recurse -File |
    Measure-Object Length -Sum).Sum
$zipBytes = (Get-Item $ZipPath).Length
$sw.Stop()
[pscustomobject]@{
    package      = $Name
    folder_GiB   = [math]::Round($folderBytes / 1GB, 2)
    zip_GiB      = [math]::Round($zipBytes / 1GB, 2)
    extract_headroom_note = "unzip needs ~$([math]::Round($folderBytes/1GB,1)) GiB free; keep ~1 GiB extra for models/data on first run"
    build_min    = [math]::Round($sw.Elapsed.TotalMinutes, 1)
    folder       = $FinalDir
    zip          = $ZipPath
} | Format-List
