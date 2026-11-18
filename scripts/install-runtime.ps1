# scripts/install-runtime.ps1 — provision the Safa runtime at install
# time (M10c §9.1 online installer).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install-runtime.ps1
#       -Variant cpu|cu124 -AppDir <dir> -DataDir <dir>
#       [-StatusFile <file>] [-KeepCache]
#
# Steps (all logged to <DataDir>\logs\install.log):
#   1. uv python install 3.11 -> <AppDir>\pyroot\cpython-3.11.*, moved to
#      <AppDir>\python (the layout Safa.exe expects); EXTERNALLY-MANAGED
#      dropped so `uv pip --system` may write site-packages
#   2. uv pip install -r requirements-<variant>.txt (frozen, hash-checked)
#      with the matching pytorch index
#   3. import sanity check, then delete the download cache (several GB —
#      useless once installed) unless -KeepCache
#
# -StatusFile: the Inno wizard polls this one-line file to draw a real
# progress bar (v0.1.1 — v0.1.0 froze on static text for 20-40 min):
#   <pid>|<step>|<steps>|<done_mb>|<total_mb>|<eta_s>|<state>|<exit>
# state = run | done | fail. <pid> lets the wizard's Cancel kill this
# process tree. done/total count bytes landed in the uv cache plus the
# python tree (download + unpack + install), so the bar moves the whole
# time; total is an estimate per variant and the bar holds at 99 % until
# the step really finishes.
#
# Everything downloads under <DataDir>\tmp — nothing goes to the system
# drive. Exit 0 on success, nonzero on failure (the installer keeps the
# log and shows it bilingually).
param(
    [ValidateSet("cpu", "cu124")]
    [string]$Variant = "cpu",
    [Parameter(Mandatory = $true)][string]$AppDir,
    [Parameter(Mandatory = $true)][string]$DataDir,
    [string]$StatusFile = "",
    [switch]$KeepCache
)

# A misquoted command line lands a literal '"' inside a path argument
# (v0.1.1: -DataDir "F:\" — the \" escaped the closing quote and $DataDir
# swallowed the rest of the line, so the script died before the transcript
# and left no log anywhere). No valid Windows path contains a quote:
# bail out before anything can throw, and leave a log OUTSIDE $DataDir so
# it is findable even when -DataDir itself is garbage.
$FallbackLog = "$env:TEMP\safa-install-args.log"
if ($AppDir.Contains('"') -or $DataDir.Contains('"')) {
    try {
        [System.IO.File]::WriteAllText($FallbackLog, (
            "Safa runtime install: a quote character arrived inside a " +
            "path argument — the installer's command line was misquoted " +
            "(old setup.exe bug). Nothing was installed.`r`n" +
            "AppDir : $AppDir`r`nDataDir: $DataDir`r`n"))
    } catch { }
    exit 3
}

$ErrorActionPreference = "Stop"

# Bytes in uv cache + python tree once step 2 is finished, measured on a
# real install (v0.1.1: cu124 has no nvidia-* wheels on Windows anymore,
# cpu ships plain onnxruntime). Only drives the bar; never a hard limit.
$ExpectedMB = @{ "cpu" = 3500; "cu124" = 11600 }
$Steps = 3

$LogDir = Join-Path $DataDir "logs"
try {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $DataDir "tmp") | Out-Null
} catch {
    # still before Start-Transcript — the fallback log is all we can leave
    try {
        [System.IO.File]::WriteAllText($FallbackLog, (
            "Safa runtime install: cannot create folders under " +
            "-DataDir '$DataDir': $_`r`n"))
    } catch { }
    exit 3
}
$env:UV_CACHE_DIR = Join-Path $DataDir "tmp\uv-cache"
$env:TEMP = Join-Path $DataDir "tmp"
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$env:PYTHONUTF8 = "1"

function Write-Status([int]$Step, [long]$DoneMB, [long]$TotalMB,
                      [long]$EtaS, [string]$State, [int]$Code = 0) {
    if (-not $StatusFile) { return }
    $line = "$PID|$Step|$Steps|$DoneMB|$TotalMB|$EtaS|$State|$Code"
    $tmp = "$StatusFile.tmp"
    try {
        [System.IO.File]::WriteAllText($tmp, $line)
        Move-Item -LiteralPath $tmp -Destination $StatusFile -Force
    } catch { }  # a busy reader must never break the install
}

function Get-TreeMB([string[]]$Dirs) {
    [long]$sum = 0
    foreach ($d in $Dirs) {
        if (-not (Test-Path -LiteralPath $d)) { continue }
        try {
            foreach ($f in [System.IO.Directory]::EnumerateFiles(
                    $d, "*", [System.IO.SearchOption]::AllDirectories)) {
                try { $sum += (New-Object System.IO.FileInfo $f).Length } catch { }
            }
        } catch { }
    }
    return [long]($sum / 1MB)
}

function Quote-Arg([string]$s) { return '"' + $s.Replace('"', '\"') + '"' }

$Log = Join-Path $LogDir "install.log"
Start-Transcript -Path $Log -Force | Out-Null
$exitCode = 1
try {
    $Uv = Join-Path $AppDir "tools\uv\uv.exe"
    if (-not (Test-Path $Uv)) { throw "uv.exe missing at $Uv" }
    $Req = Join-Path $AppDir "requirements-$Variant.txt"
    if (-not (Test-Path $Req)) { throw "requirements missing: $Req" }

    Write-Host "== Safa runtime install — variant $Variant =="
    Write-Host "AppDir : $AppDir"
    Write-Host "DataDir: $DataDir"

    Write-Status 1 0 0 -1 "run"
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
    # --no-config: {app} ships pyproject.toml, whose [tool.uv] override
    # would DROP onnxruntime from the cpu requirements (v0.1.1 swap).
    $uvArgs = @(
        # --no-deps: the export IS the full locked closure. Without it uv
        # re-resolves and faster-whisper's own `onnxruntime` requirement
        # fails on cu124 (onnxruntime-gpu provides the module).
        "pip", "install", "--no-config", "--no-deps",
        "--python", $Py, "--system",
        "-r", $Req,
        "--require-hashes",
        "--extra-index-url", "https://download.pytorch.org/whl/$Variant",
        "--index-strategy", "unsafe-best-match",
        "--cache-dir", $env:UV_CACHE_DIR
    )
    # Run uv as a child we can watch: the transcript does not see native
    # output of a Start-Process child, so it goes to files appended below.
    $uvOut = Join-Path $LogDir "uv-pip.out.txt"
    $uvErr = Join-Path $LogDir "uv-pip.err.txt"
    $watch = @($env:UV_CACHE_DIR, $PyDir)
    $baseMB = Get-TreeMB $watch
    $totalMB = $ExpectedMB[$Variant]
    $proc = Start-Process -FilePath $Uv -NoNewWindow -PassThru `
        -ArgumentList (($uvArgs | ForEach-Object { Quote-Arg $_ }) -join " ") `
        -RedirectStandardOutput $uvOut -RedirectStandardError $uvErr
    $null = $proc.Handle  # keep the handle so ExitCode survives exit
    $t0 = Get-Date
    [long]$done = 0
    Write-Status 2 0 $totalMB -1 "run"
    while (-not $proc.WaitForExit(2000)) {
        # never move backwards: uv deletes each downloaded archive after
        # unpacking it, so the raw size dips (2.2 GB -> 1.3 GB on a real
        # cu124 install)
        $done = [Math]::Max($done, (Get-TreeMB $watch) - $baseMB)
        $now = Get-Date
        $eta = -1
        $elapsed = ($now - $t0).TotalSeconds
        # no guess before 5 % is in: the first real cu124 guess was 11 h
        if ($elapsed -ge 15 -and $done -ge ($totalMB * 0.05)) {
            # whole-step average: uv downloads in parallel bursts and then
            # unpacks, so a short window swings wildly (822 s -> 32 s -> 113 s
            # on a real cpu install); the average settles instead
            $rate = $done / $elapsed   # MB/s
            if ($rate -gt 0.05) {
                $eta = [long](([Math]::Max(0, $totalMB - $done)) / $rate)
            }
        }
        Write-Status 2 ([Math]::Min($done, [long]($totalMB * 0.99))) $totalMB $eta "run"
    }
    foreach ($f in @($uvOut, $uvErr)) {
        if (Test-Path $f) { Get-Content -LiteralPath $f | Write-Host }
    }
    if ($proc.ExitCode -ne 0) { throw "uv pip install failed ($($proc.ExitCode))" }
    Write-Host ("   landed {0} MB (estimate {1} MB)" -f
        ((Get-TreeMB $watch) - $baseMB), $totalMB)
    Write-Status 2 $totalMB $totalMB 0 "run"

    Write-Status 3 0 0 -1 "run"
    Write-Host "== sanity check =="
    & $Py -W ignore -c "import fastapi, uvicorn, torch, onnxruntime, faster_whisper, audio_separator, silero_vad, df, pedalboard; print('ok')"
    if ($LASTEXITCODE -ne 0) { throw "import sanity check failed" }

    if (-not $KeepCache) {
        Write-Host "== removing download cache (no longer needed) =="
        Remove-Item -LiteralPath $env:UV_CACHE_DIR -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
    Write-Host "== done =="
    $exitCode = 0
} catch {
    Write-Host "INSTALL FAILED: $_"
    $exitCode = 1
} finally {
    Write-Status $Steps 0 0 0 $(if ($exitCode -eq 0) { "done" } else { "fail" }) $exitCode
    Stop-Transcript | Out-Null
}
exit $exitCode
