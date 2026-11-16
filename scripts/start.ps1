# scripts/start.ps1 — Sound System launcher (M10 T1).
#   checks env/port/disk → repairs setup → starts uvicorn → opens browser
# Single-instance: if our app already answers on the port, only the
# browser opens and this exits 0 — never a second server.
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
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "launcher-lib.ps1")

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# chcp 65001 equivalent + UTF-8 Python — Arabic filenames/paths must
# survive every layer (M10 T1).
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Keep C: clean — same redirect as setup.ps1 and the app itself (T2).
$TmpDir = Join-Path $Root "data\tmp"
New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null
$env:UV_CACHE_DIR = $TmpDir
$env:npm_config_cache = $TmpDir
$env:TEMP = $TmpDir
$env:TMP = $TmpDir
$env:TMPDIR = $TmpDir

# ---- port -----------------------------------------------------------------
# -Port and SOUND_SYSTEM_PORT both go through the validator — a raw
# value is never interpolated into URLs or commands.
$rawPort = if ($Port -gt 0) { "$Port" } else { $env:SOUND_SYSTEM_PORT }
$validPort = Test-ValidPort $rawPort
if ($null -eq $validPort) {
    Write-Bilingual `
        "Invalid SOUND_SYSTEM_PORT '$rawPort' — must be a number between 1024 and 65535." `
        "قيمة SOUND_SYSTEM_PORT «$rawPort» غير صالحة — يجب أن تكون رقمًا بين 1024 و65535." `
        Red
    exit 2
}
$Port = $validPort

$Base = "http://127.0.0.1:$Port"

# ---- warnings (non-fatal) ---------------------------------------------------
if ($Root -match "OneDrive") {
    Write-Bilingual `
        "Warning: the app lives inside OneDrive — files may sync or lock while it runs. Moving it outside OneDrive avoids weird errors." `
        "تنبيه: التطبيق داخل مجلد OneDrive — قد تتزامن الملفات أو تُقفل أثناء العمل. انقل المجلد خارج OneDrive لتجنب أخطاء غريبة."
}
if ($Root.Length -gt 80) {
    Write-Bilingual `
        "Warning: the app path is very long ($($Root.Length) characters) — some tools may fail. A shorter path like D:\SoundSystem is safer." `
        "تنبيه: مسار التطبيق طويل جدًا ($($Root.Length) حرفًا) — قد تفشل بعض الأدوات. مسار أقصر مثل D:\SoundSystem أكثر أمانًا."
}
try {
    $freeGB = Get-FreeSpaceGB $Root
    if ($freeGB -lt 5) {
        Write-Bilingual `
            "Warning: only ${freeGB} GB free on this drive — models and results need a few GB. Free some space if downloads fail." `
            "تنبيه: المتبقي ${freeGB} غيغابايت فقط على هذا القرص — النماذج والنتائج تحتاج بضعة غيغابايت. حرّر مساحة إذا فشل التنزيل."
    }
} catch {}

# ---- single instance --------------------------------------------------------
$State = Get-PortState -Port $Port
if ($CheckOnly) {
    Write-Output "STATE:$State"
    exit 0
}
if ($State -eq "ours") {
    Write-Bilingual `
        "Sound System is already running — opening it." `
        "Sound System يعمل بالفعل — جارٍ فتحه." Cyan
    if (-not $NoBrowser) { Start-Process $Base }
    exit 0
}
if ($State -eq "busy") {
    Write-Bilingual `
        "Port $Port is in use by another program. Close it, or set SOUND_SYSTEM_PORT to another port and run again." `
        "المنفذ $Port مستخدم من برنامج آخر. أغلقه، أو عيّن SOUND_SYSTEM_PORT إلى منفذ آخر وأعد المحاولة." Red
    exit 3
}

# ---- repair checks -----------------------------------------------------------
$NeedSetup = $false
if (-not (Test-Path (Join-Path $Root ".venv\Scripts\python.exe"))) {
    $NeedSetup = $true
}
if (-not (Test-Path (Join-Path $Root "tools\ffmpeg\bin\ffmpeg.exe"))) {
    $NeedSetup = $true
}
if (-not (Test-Path (Join-Path $Root "tools\ffmpeg\bin\ffprobe.exe"))) {
    $NeedSetup = $true
}
if ($NeedSetup) {
    Write-Bilingual `
        "First-time setup (or repair) — this downloads what is missing and can take a while." `
        "الإعداد لأول مرة (أو الإصلاح) — ينزّل ما هو ناقص وقد يستغرق بعض الوقت." Cyan
    $setup = Join-Path $Root "scripts\setup.ps1"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $setup
    if ($LASTEXITCODE -ne 0) {
        Write-Bilingual `
            "Setup failed — see the messages above, then run scripts\setup.ps1 again." `
            "فشل الإعداد — راجع الرسائل أعلاه ثم أعد تشغيل scripts\setup.ps1." Red
        exit 4
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Bilingual `
        "'uv' is not installed or not on PATH. Install it once (see README.md), then run again." `
        "أداة «uv» غير مثبتة أو غير موجودة في PATH. ثبّتها مرة واحدة (راجع README.md) ثم أعد المحاولة." Red
    exit 5
}

if (Test-NeedUvSync -Root $Root) {
    Write-Host "== uv sync =="
    & uv sync
    if ($LASTEXITCODE -ne 0) {
        Write-Bilingual `
            "uv sync failed — run scripts\setup.ps1 to repair." `
            "فشل uv sync — شغّل scripts\setup.ps1 للإصلاح." Red
        exit 5
    }
}

if (Test-StaleBuild -Root $Root) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Bilingual `
            "The app needs a rebuild but Node.js (npm) is missing. Install Node.js LTS once, or run scripts\setup.ps1." `
            "التطبيق يحتاج إعادة بناء لكن Node.js (npm) غير موجود. ثبّت Node.js LTS مرة واحدة، أو شغّل scripts\setup.ps1." Red
        exit 6
    }
    Write-Host "== frontend build =="
    Push-Location (Join-Path $Root "frontend")
    try {
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    } catch {
        Pop-Location
        Write-Bilingual `
            "Frontend build failed — run scripts\setup.ps1 to repair." `
            "فشل بناء الواجهة — شغّل scripts\setup.ps1 للإصلاح." Red
        exit 6
    }
    Pop-Location
}

# ---- start -------------------------------------------------------------------
$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
$proc = Start-Process -FilePath $PythonExe `
    -ArgumentList @(
        "-m", "uvicorn", "backend.main:app",
        "--host", "127.0.0.1", "--port", "$Port"
    ) `
    -WorkingDirectory $Root -NoNewWindow -PassThru

# Health wait: up to 90 s — antivirus cold-starts a fresh venv slowly.
$healthy = $false
for ($i = 0; $i -lt 180 -and -not $proc.HasExited; $i++) {
    try {
        $r = Invoke-RestMethod -Uri "$Base/api/health" -TimeoutSec 2 `
            -ErrorAction Stop
        if ($r.status -eq "ok") { $healthy = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 500
}
if (-not $healthy) {
    if (-not $proc.HasExited) {
        # taskkill /T kills the whole child tree, never orphans.
        & taskkill /T /F /PID $proc.Id 2>$null | Out-Null
    }
    Write-Bilingual `
        "The app did not start within 90 seconds — run scripts\setup.ps1 to repair, or check data\logs\app.log." `
        "لم يبدأ التطبيق خلال 90 ثانية — شغّل scripts\setup.ps1 للإصلاح، أو راجع data\logs\app.log." Red
    exit 7
}

if (-not $NoBrowser) { Start-Process $Base }

# Foreground lifetime = server lifetime. Ctrl+C reaches uvicorn too
# (same console process group) → its shutdown path cleans up workers.
try {
    Wait-Process -Id $proc.Id
} finally {
    if (-not $proc.HasExited) {
        & taskkill /T /F /PID $proc.Id 2>$null | Out-Null
    }
}
exit $proc.ExitCode
