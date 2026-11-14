# scripts/setup.ps1 — idempotent environment setup.
# 1) uv sync (creates .venv with pinned Python 3.11 + all deps)
# 2) ensure tools/ffmpeg/bin/{ffmpeg,ffprobe}.exe (downloads pinned static build)
# Safe to re-run.
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "== uv sync =="
uv sync
if ($LASTEXITCODE -ne 0) { throw "uv sync failed ($LASTEXITCODE)" }

$FfmpegDir = Join-Path $Root "tools\ffmpeg"
$FfmpegBin = Join-Path $FfmpegDir "bin"
$FfmpegExe = Join-Path $FfmpegBin "ffmpeg.exe"

if (-not (Test-Path $FfmpegExe)) {
    $Url = "https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip"
    $Tmp = Join-Path $env:TEMP ("ffmpeg-dl-" + [Guid]::NewGuid().ToString("N"))
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

Write-Host "== setup done =="
