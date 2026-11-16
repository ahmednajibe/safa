# scripts/build-installer.ps1 — build Safa Inno installers (M10c §9).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-installer.ps1 -Kind online
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-installer.ps1 -Kind cpu
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-installer.ps1 -Kind gpu [-SliceSizeMB 64]
#
# online : small payload (app + ffmpeg + uv.exe + frozen requirements);
#          the wizard downloads the runtime at install time.
# cpu    : self-contained exe < 2 GiB (python + wheels + core models).
# gpu    : same for CUDA 12.4, Inno disk spanning -> setup.exe + -N.bin.
#
# ISCC comes from tools\innosetup\app\ISCC.exe (see README "from source").
# PS 5.1 compatible; everything temp stays under data\tmp; output lands
# in dist_pkg\ (gitignored).
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("online", "cpu", "gpu")]
    [string]$Kind,
    # tests only: force spanning with a small payload
    [int]$SliceSizeMB = 0,
    # reuse an existing dist_pkg stage folder instead of repackaging
    [string]$StageDir = ""
)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
. (Join-Path $PSScriptRoot "launcher-lib.ps1")

$TmpRoot = Join-Path $Root "data\tmp\instbuild"
New-Item -ItemType Directory -Force -Path $TmpRoot | Out-Null
$env:UV_CACHE_DIR = Join-Path $Root "data\tmp\uv-cache"
$env:TEMP = $TmpRoot
$env:TMP = $TmpRoot
$env:TMPDIR = $TmpRoot
$env:PYTHONUTF8 = "1"

$DistDir = Join-Path $Root "dist_pkg"
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

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

function Copy-Tree([string]$Src, [string]$Dst) {
    & robocopy $Src $Dst /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "robocopy $Src -> $Dst failed ($LASTEXITCODE)" }
}

# ---- metadata from release-info.toml via the canonical module -------------
$PyExe = "uv"
function Get-ReleaseInfo([string]$Key) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & uv run --no-sync python -m backend.core.release_info $Key 2>$null
        if ($LASTEXITCODE -ne 0) { throw "release_info $Key failed" }
        return ($out -split "`n" | Select-Object -First 1).Trim()
    } finally {
        $ErrorActionPreference = $prev
    }
}

$Version = Get-ReleaseInfo "version"
$RepoUrl = Get-ReleaseInfo "repo"
$GuidKey = @{ online = "online"; cpu = "offline_cpu"; gpu = "offline_gpu" }[$Kind]
$GuidLine = (& uv run --no-sync python -m backend.core.release_info guid 2>$null |
    Where-Object { $_ -match "^$GuidKey=" })
# Inno wants the GUID bare — the .iss wraps it in {{ }} literals.
$Guid = $GuidLine.Split("=", 2)[1].Trim() -replace '[{}]', ''
if (-not $Guid) { throw "guid for $GuidKey not found in release-info.toml" }

$Iscc = Join-Path $Root "tools\innosetup\app\ISCC.exe"
if (-not (Test-Path $Iscc)) {
    throw "ISCC.exe not found at $Iscc - download Inno Setup 6 (see docs)"
}

# Safa.exe is a gitignored build output — compile when missing/stale.
if (Test-SafaLauncherStale (Join-Path $Root "packaging\windows\launcher")) {
    Write-Host "== compile Safa.exe launcher =="
    Invoke-Native {
        powershell -NoProfile -ExecutionPolicy Bypass -File `
            (Join-Path $PSScriptRoot "build-launcher.ps1")
    } "build-launcher.ps1"
}

Write-Host "== build-installer $Kind v$Version =="

# ---- stage -------------------------------------------------------------------
if ($Kind -eq "online") {
    # Small payload: app + uv + frozen requirements (no python/, no models/).
    $Stage = Join-Path $TmpRoot "online-$Version"
    if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
    $Payload = Join-Path $Stage "payload"
    New-Item -ItemType Directory -Force -Path $Payload | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $Stage "launcher") | Out-Null

    if (Test-StaleBuild -Root $Root) {
        Write-Host "== frontend build (stale) =="
        Push-Location (Join-Path $Root "frontend")
        try { Invoke-Native { npm run build } "npm run build" }
        finally { Pop-Location }
    }
    Copy-Tree (Join-Path $Root "backend") (Join-Path $Payload "backend")
    Copy-Tree (Join-Path $Root "frontend\dist") (Join-Path $Payload "frontend\dist")
    Copy-Tree (Join-Path $Root "tools\ffmpeg") (Join-Path $Payload "tools\ffmpeg")

    $UvDir = Join-Path $Payload "tools\uv"
    New-Item -ItemType Directory -Force -Path $UvDir | Out-Null
    $UvExe = (Get-Command uv -ErrorAction Stop).Source
    Copy-Item $UvExe (Join-Path $UvDir "uv.exe")

    $ScriptsDir = Join-Path $Payload "scripts"
    New-Item -ItemType Directory -Force -Path $ScriptsDir | Out-Null
    foreach ($f in @("install-runtime.ps1", "start.ps1", "launcher-lib.ps1", "prefetch_models.py")) {
        Copy-Item (Join-Path $Root "scripts\$f") (Join-Path $ScriptsDir $f)
    }
    foreach ($f in @("start.bat", "start.sh", "start.command",
                     "pyproject.toml", "uv.lock",
                     "README.md", "README.ar.md",
                     "LICENSE", "THIRD_PARTY_NOTICES.txt")) {
        $src = Join-Path $Root $f
        if (Test-Path $src) { Copy-Item $src (Join-Path $Payload $f) }
    }

    Write-Host "== uv export requirements (frozen, hashed) =="
    foreach ($extra in @("cpu", "cu124")) {
        Invoke-Native {
            uv export --frozen --no-dev --extra $extra `
                -o (Join-Path $Payload "requirements-$extra.txt")
        } "uv export --extra $extra"
    }

    Copy-Item (Join-Path $Root "packaging\windows\launcher\Safa.exe") `
        (Join-Path $Stage "launcher\Safa.exe")
    Copy-Item (Join-Path $Root "packaging\windows\launcher\safa.ico") `
        (Join-Path $Stage "launcher\safa.ico")

    $Iss = Join-Path $Root "packaging\windows\inno\online.iss"
} else {
    # Offline: full runtime stage (package.ps1 -StageOnly) + core models,
    # minus the portable marker - installed.json drives data location.
    if ($StageDir -and (Test-Path $StageDir)) {
        $PkgStage = $StageDir
    } else {
        Write-Host "== package runtime stage ($Kind) =="
        Invoke-Native {
            powershell -NoProfile -ExecutionPolicy Bypass -File `
                (Join-Path $PSScriptRoot "package.ps1") `
                -Variant $Kind -StageOnly
        } "package.ps1 -Variant $Kind -StageOnly"
        $PkgStage = Get-ChildItem $DistDir -Directory |
            Where-Object { $_.Name -like "Safa-$Version-win-x64-$Kind*" } |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (-not $PkgStage) { throw "package.ps1 produced no stage folder" }
        $PkgStage = $PkgStage.FullName
    }
    Write-Host "   stage: $PkgStage"

    $Stage = Join-Path $TmpRoot "$Kind-offline-$Version"
    if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
    New-Item -ItemType Directory -Force -Path $Stage | Out-Null
    Copy-Tree $PkgStage (Join-Path $Stage "stage")

    # installed, not portable: the marker would pin data to {app}\data.
    Remove-Item -Force (Join-Path $Stage "stage\portable.txt") -ErrorAction SilentlyContinue

    Write-Host "== stage core models =="
    $CoreNames = (& uv run --no-sync python -m backend.core.release_info model_packs 2>$null |
        Where-Object { $_ -match "^core=" }).Split("=", 2)[1].Trim()
    if (-not $CoreNames) { throw "core model pack keys missing" }
    Invoke-Native {
        uv run --no-sync python -m backend.core.model_pack stage `
            (Join-Path $Stage "stage\models") `
            --models-dir (Join-Path $Root "models") `
            --names $CoreNames
    } "model_pack stage"

    Copy-Item (Join-Path $Root "packaging\windows\launcher\safa.ico") `
        (Join-Path $Stage "Safa.ico")
    $Iss = Join-Path $Root "packaging\windows\inno\offline-$Kind.iss"
}

# ---- compile -------------------------------------------------------------------
Write-Host "== ISCC $Kind =="
$isccArgs = @(
    "/DAppVer=$Version",
    "/DAppGuid=$Guid",
    "/DSourceDir=$Stage",
    "/DOutDir=$DistDir",
    "/DRepoUrl=$RepoUrl"
)
if ($Kind -eq "gpu" -and $SliceSizeMB -gt 0) {
    $isccArgs += "/DSliceSize=$($SliceSizeMB * 1MB)"
}
Invoke-Native { & $Iscc @isccArgs $Iss } "ISCC $Kind"

if ($Kind -eq "gpu") {
    # slicecount.txt next to the exe: the installer's InitializeSetup uses
    # it to refuse missing -N.bin slices before the wizard even starts
    # (Inno's own "insert disk" prompt cannot abort a silent install).
    $slices = Get-ChildItem $DistDir -File -Filter "Safa-$Version-win-x64-gpu-offline-setup-*.bin"
    if ($slices.Count -lt 1) { throw "gpu build produced no -N.bin slices" }
    $tooBig = $slices | Where-Object { $_.Length -gt 1932735283 }
    if ($tooBig) { throw "slice exceeds 1.8 GiB: $($tooBig.Name)" }
    "$($slices.Count)" | Set-Content (Join-Path $DistDir "slicecount.txt") -Encoding ASCII
    Write-Host "   $($slices.Count) slice(s) <= 1.8 GiB; slicecount.txt written"
}

$outputs = Get-ChildItem $DistDir -File |
    Where-Object { $_.Name -like "*$Kind*" -and $_.LastWriteTime -gt (Get-Date).AddMinutes(-60) }
$sw = [pscustomobject]@{
    kind    = $Kind
    outputs = ($outputs | ForEach-Object { "{0} ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB) }) -join "; "
}
$sw | Format-List
