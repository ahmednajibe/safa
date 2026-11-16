# scripts/launcher-lib.ps1 — pure, testable launcher helpers (M10 T1).
# Dot-sourced by scripts/start.ps1; unit-tested from Python via
#   powershell -NoProfile -Command ". .\launcher-lib.ps1; <fn> ..."
# Keep every function free of side effects except Read*/Get-* probes.

# Print both EN and AR lines (AR first on Arabic UI cultures). The
# launcher must never assume the reader speaks English.
function Write-Bilingual {
    param([string]$En, [string]$Ar, [string]$Color = "Yellow")
    if ((Get-Culture).Name -like "ar*") {
        Write-Host $Ar -ForegroundColor $Color
        Write-Host $En -ForegroundColor $Color
    } else {
        Write-Host $En -ForegroundColor $Color
        Write-Host $Ar -ForegroundColor $Color
    }
}

# SOUND_SYSTEM_PORT → int 1024–65535, or $null when unparsable/out of
# range. Never interpolate a raw value into URLs/commands — always go
# through this validator first.
function Test-ValidPort {
    param($Value)
    if ($null -eq $Value -or "$Value".Trim() -eq "") { return 8790 }
    $n = 0
    if (-not [int]::TryParse("$Value".Trim(), [ref]$n)) { return $null }
    if ($n -lt 1024 -or $n -gt 65535) { return $null }
    return $n
}

# True when http://127.0.0.1:$Port/api/health answers with *our* app's
# health payload (status=ok + python key — another program holding the
# port will not return this shape).
function Test-OurApp {
    param([int]$Port, [int]$TimeoutSec = 2)
    try {
        $r = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$Port/api/health" `
            -TimeoutSec $TimeoutSec -ErrorAction Stop
        return ($r.status -eq "ok" -and $null -ne $r.python)
    } catch {
        return $false
    }
}

# True when nothing listens on 127.0.0.1:$Port (a bind probe — the only
# race-free way to ask the OS; SO_REUSEADDR is not used anywhere here).
function Test-PortFree {
    param([int]$Port)
    try {
        $l = New-Object System.Net.Sockets.TcpListener(
            [System.Net.IPAddress]::Parse("127.0.0.1"), $Port)
        $l.Start()
        $l.Stop()
        return $true
    } catch {
        return $false
    }
}

# Single-instance state: "ours" (our app already serves → just open the
# browser), "busy" (a foreign program owns the port), "free".
function Get-PortState {
    param([int]$Port)
    if (Test-OurApp -Port $Port) { return "ours" }
    if (Test-PortFree -Port $Port) { return "free" }
    return "busy"
}

# True when the built frontend is older than any tracked source — the
# launcher then runs `npm run build` before starting. Tracked inputs:
# frontend/src/**, frontend/public/**, frontend/index.html,
# vite.config.ts, tsconfig*.json, package.json, package-lock.json.
function Test-StaleBuild {
    param([string]$Root)
    $index = Join-Path $Root "frontend\dist\index.html"
    if (-not (Test-Path $index)) { return $true }
    $stamp = (Get-Item $index).LastWriteTimeUtc
    $fe = Join-Path $Root "frontend"
    foreach ($dir in @("src", "public")) {
        $p = Join-Path $fe $dir
        if (Test-Path $p) {
            $newer = Get-ChildItem -Path $p -Recurse -File |
                Where-Object { $_.LastWriteTimeUtc -gt $stamp } |
                Select-Object -First 1
            if ($null -ne $newer) { return $true }
        }
    }
    foreach ($f in @(
        "index.html", "vite.config.ts", "package.json",
        "package-lock.json"
    )) {
        $p = Join-Path $fe $f
        if ((Test-Path $p) -and
            ((Get-Item $p).LastWriteTimeUtc -gt $stamp)) {
            return $true
        }
    }
    $staleTs = Get-ChildItem -Path $fe -Filter "tsconfig*.json" -File |
        Where-Object { $_.LastWriteTimeUtc -gt $stamp } |
        Select-Object -First 1
    return ($null -ne $staleTs)
}

# True when uv.lock / pyproject.toml changed after the venv was created
# (or the venv is missing) → the launcher runs `uv sync` first.
function Test-NeedUvSync {
    param([string]$Root)
    $venv = Join-Path $Root ".venv"
    $marker = Join-Path $venv "pyvenv.cfg"
    if (-not (Test-Path $marker)) { return $true }
    $stamp = (Get-Item $marker).LastWriteTimeUtc
    foreach ($f in @("uv.lock", "pyproject.toml")) {
        $p = Join-Path $Root $f
        if ((Test-Path $p) -and
            ((Get-Item $p).LastWriteTimeUtc -gt $stamp)) {
            return $true
        }
    }
    return $false
}

# Free space in GB on the drive that hosts $Path (warn threshold lives
# in start.ps1 — this stays a pure probe).
function Get-FreeSpaceGB {
    param([string]$Path)
    $drive = (Split-Path -Qualifier (Resolve-Path $Path).Path)
    $d = New-Object System.IO.DriveInfo($drive)
    return [math]::Round($d.AvailableFreeSpace / 1GB, 2)
}
