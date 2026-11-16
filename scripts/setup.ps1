# scripts/setup.ps1 — idempotent environment setup.
# 1) uv sync --extra cu124 (creates .venv with pinned Python 3.11 + the
#    CUDA 12.4 wheel set — the dev variant for this machine)
# 2) ensure tools/ffmpeg/bin/{ffmpeg,ffprobe}.exe (downloads pinned static build)
# 3) frontend build
# 4) prefetch the core model set (M10) — -AllModels adds whisper_turbo +
#    kim_vocal_2 (≈1.7 GB extra).
# Safe to re-run.
param(
    [switch]$AllModels
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

# Keep C: clean (M10): every cache/temp dir lives under <root>\data\tmp so
# installing on another drive never fills the system drive.
$TmpDir = Join-Path $Root "data\tmp"
New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null
$env:UV_CACHE_DIR = $TmpDir
$env:npm_config_cache = $TmpDir
$env:TEMP = $TmpDir
$env:TMP = $TmpDir
$env:TMPDIR = $TmpDir
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

Write-Host "== uv sync --extra cu124 =="
# The cu124 extra selects the CUDA 12.4 torch/onnxruntime-gpu wheel set.
# A bare `uv sync`/`uv run` would resync to the CPU baseline and silently
# strip the GPU wheels — always pass the extra on this machine.
uv sync --extra cu124
if ($LASTEXITCODE -ne 0) { throw "uv sync failed ($LASTEXITCODE)" }

$FfmpegDir = Join-Path $Root "tools\ffmpeg"
$FfmpegBin = Join-Path $FfmpegDir "bin"
$FfmpegExe = Join-Path $FfmpegBin "ffmpeg.exe"

if (-not (Test-Path $FfmpegExe)) {
    $Url = "https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip"
    $Tmp = Join-Path $TmpDir ("ffmpeg-dl-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $Tmp | Out-Null
    try {
        $ZipPath = Join-Path $Tmp "ffmpeg.zip"
        Write-Host "== Downloading ffmpeg 7.1.1 essentials =="
        Invoke-WebRequest -Uri $Url -OutFile $ZipPath -UseBasicParsing
        Expand-Archive -Path $ZipPath -DestinationPath $Tmp -Force
        # Zip contains ffmpeg-7.1.1-essentials_build\bin\*.exe — copy bin/ into tools/ffmpeg/.
        $Inner = Get-ChildItem -Path $Tmp -Directory |
            Where-Object { Test-Path (Join-Path $_.FullName "bin\ffmpeg.exe") } |
            Select-Object -First 1
        if (-not $Inner) { throw "ffmpeg zip layout unexpected: no inner bin\ffmpeg.exe" }
        New-Item -ItemType Directory -Path $FfmpegDir -Force | Out-Null
        Copy-Item -Path (Join-Path $Inner.FullName "bin") -Destination $FfmpegBin -Recurse -Force
    } finally {
        Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "== ffmpeg already present =="
}

if (-not (Test-Path (Join-Path $FfmpegBin "ffprobe.exe"))) {
    throw "ffprobe.exe missing in $FfmpegBin"
}

& $FfmpegExe -version | Select-Object -First 1
& (Join-Path $FfmpegBin "ffprobe.exe") -version | Select-Object -First 1

$FrontendDir = Join-Path $Root "frontend"
if (Test-Path $FrontendDir) {
    Write-Host "== frontend build =="
    Push-Location $FrontendDir
    try {
        if (Test-Path (Join-Path $FrontendDir "package-lock.json")) {
            npm ci
        } else {
            npm install
        }
        if ($LASTEXITCODE -ne 0) { throw "npm install/ci failed ($LASTEXITCODE)" }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
}

# M10 T3: prefetch the core model set (deepfilternet3, htdemucs,
# whisper_small — silero ships bundled). Optional models via -AllModels
# or the app's Download buttons. A failed prefetch is a warning, not a
# fatal error: the app downloads missing models on demand.
Write-Host "== models =="
$PrefetchArgs = @("run", "--extra", "cu124", "python", "scripts\prefetch_models.py")
if ($AllModels) { $PrefetchArgs += "--all" }
& uv @PrefetchArgs
if ($LASTEXITCODE -ne 0) {
    Write-Warning "model prefetch failed ($LASTEXITCODE) — the app can download models on first use."
}

Write-Host "== setup done =="
