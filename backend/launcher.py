"""Safa launcher — one entry point for start.bat / start.ps1 / start.sh /
start.command / Safa.exe (M10c §3, ported from scripts/start.ps1).

  python -m backend.launcher [--port N] [--no-browser] [--check-only]
  python -m backend.launcher models import <zip> [--models-dir D]

Flow: validate port → warn (OneDrive/long path/low disk) → single
instance via /api/health identity → portable vs dev handling → spawn
uvicorn → health-wait → open browser → wait on the server.

Exit codes (same contract as the old start.ps1):
  0 ok / already running · 2 bad port · 3 port busy · 4 broken setup ·
  5 uv missing/uv sync failed · 6 frontend build failed · 7 health timeout
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8790
HEALTH_TIMEOUT_S = 90
LOW_DISK_GB = 5
LONG_PATH_CHARS = 80

# Bilingual strings — every user-facing line carries EN + AR (the AR line
# prints first on Arabic locales, matching the old Write-Bilingual).
_STRINGS = {
    "bad_port": (
        "Invalid SOUND_SYSTEM_PORT '{raw}' — must be a number between "
        "1024 and 65535.",
        "قيمة SOUND_SYSTEM_PORT «{raw}» غير صالحة — يجب أن تكون رقمًا بين "
        "1024 و65535.",
    ),
    "onedrive": (
        "Warning: the app lives inside OneDrive — files may sync or lock "
        "while it runs. Moving it outside OneDrive avoids weird errors.",
        "تنبيه: التطبيق داخل مجلد OneDrive — قد تتزامن الملفات أو تُقفل "
        "أثناء العمل. انقل المجلد خارج OneDrive لتجنب أخطاء غريبة.",
    ),
    "longpath": (
        "Warning: the app path is very long ({n} characters) — some tools "
        "may fail. A shorter path like D:\\Safa is safer.",
        "تنبيه: مسار التطبيق طويل جدًا ({n} حرفًا) — قد تفشل بعض الأدوات. "
        "مسار أقصر مثل D:\\Safa أكثر أمانًا.",
    ),
    "lowdisk": (
        "Warning: only {gb} GB free on this drive — models and results "
        "need a few GB. Free some space if downloads fail.",
        "تنبيه: المتبقي {gb} غيغابايت فقط على هذا القرص — النماذج "
        "والنتائج تحتاج بضعة غيغابايت. حرّر مساحة إذا فشل التنزيل.",
    ),
    "already_running": (
        "Safa is already running — opening it.",
        "صفا يعمل بالفعل — جارٍ فتحه.",
    ),
    "port_busy": (
        "Port {port} is in use by another program. Close it, or set "
        "SOUND_SYSTEM_PORT to another port and run again.",
        "المنفذ {port} مستخدم من برنامج آخر. أغلقه، أو عيّن "
        "SOUND_SYSTEM_PORT إلى منفذ آخر وأعد المحاولة.",
    ),
    "incomplete_pkg": (
        "This package is incomplete (python runtime missing). Extract "
        "the zip again into a fresh folder.",
        "هذه الحزمة غير مكتملة (ملف بايثون مفقود). فكّ ضغط الملف مرة أخرى "
        "في مجلد جديد.",
    ),
    "ffmpeg_missing": (
        "ffmpeg is missing from this package. Extract the zip again into "
        "a fresh folder.",
        "ffmpeg غير موجود في هذه الحزمة. فكّ ضغط الملف مرة أخرى في مجلد "
        "جديد.",
    ),
    "unblocking": (
        "First start — unblocking files that came from a zip (about a "
        "minute, once).",
        "أول تشغيل — إزالة حظر الملفات القادمة من ملف مضغوط (حوالي دقيقة، "
        "مرة واحدة).",
    ),
    "checking_models": (
        "Checking AI models — the first run downloads about 600 MB once.",
        "التحقق من نماذج الذكاء الاصطناعي — أول تشغيل ينزّل حوالي 600 "
        "ميغابايت مرة واحدة.",
    ),
    "models_partial": (
        "Some models could not be downloaded yet — you can retry inside "
        "the app.",
        "تعذّر تنزيل بعض النماذج الآن — يمكنك إعادة المحاولة من داخل "
        "التطبيق.",
    ),
    "setup_repair": (
        "First-time setup (or repair) — this downloads what is missing "
        "and can take a while.",
        "الإعداد لأول مرة (أو الإصلاح) — ينزّل ما هو ناقص وقد يستغرق بعض "
        "الوقت.",
    ),
    "setup_failed": (
        "Setup failed — see the messages above, then run the setup "
        "script again.",
        "فشل الإعداد — راجع الرسائل أعلاه ثم أعد تشغيل سكربت الإعداد.",
    ),
    "uv_missing": (
        "'uv' is not installed or not on PATH. Install it once (see "
        "README.md), then run again.",
        "أداة «uv» غير مثبتة أو غير موجودة في PATH. ثبّتها مرة واحدة "
        "(راجع README.md) ثم أعد المحاولة.",
    ),
    "uv_sync_failed": (
        "uv sync failed — run the setup script to repair.",
        "فشل uv sync — شغّل سكربت الإعداد للإصلاح.",
    ),
    "npm_missing": (
        "The app needs a rebuild but Node.js (npm) is missing. Install "
        "Node.js LTS once, or run the setup script.",
        "التطبيق يحتاج إعادة بناء لكن Node.js (npm) غير موجود. ثبّت "
        "Node.js LTS مرة واحدة، أو شغّل سكربت الإعداد.",
    ),
    "build_failed": (
        "Frontend build failed — run the setup script to repair.",
        "فشل بناء الواجهة — شغّل سكربت الإعداد للإصلاح.",
    ),
    "health_timeout": (
        "The app did not start within {n} seconds — run the setup "
        "script to repair, or check the logs under data/logs.",
        "لم يبدأ التطبيق خلال {n} ثانية — شغّل سكربت الإعداد للإصلاح، أو "
        "راجع السجلات في data/logs.",
    ),
    "shortcut_created": (
        "Desktop shortcut created — delete it any time; we will not "
        "recreate it.",
        "تم إنشاء اختصار على سطح المكتب — احذفه متى شئت؛ لن نعيد إنشاءه.",
    ),
}


# ---- output ----------------------------------------------------------------


def _arabic_locale() -> bool:
    lang = os.environ.get("LANG") or os.environ.get("LC_ALL") or ""
    if lang.lower().startswith("ar"):
        return True
    try:
        import locale

        loc = locale.getlocale()[0] or locale.getdefaultlocale()[0] or ""
        return loc.lower().startswith("ar")
    except Exception:
        return False


def bilingual(key: str, color: str = "", **kw) -> None:
    """Print the EN+AR pair for ``key`` (AR first on Arabic locales)."""
    en, ar = _STRINGS[key]
    en, ar = en.format(**kw), ar.format(**kw)
    for line in ((ar, en) if _arabic_locale() else (en, ar)):
        print(line, flush=True)


# ---- pure probes ------------------------------------------------------------


def valid_port(raw) -> int | None:
    """``SOUND_SYSTEM_PORT`` → int 1024–65535; ``None`` when unparsable or
    out of range. Empty/None → the default port (never interpolate raw
    values into URLs — always go through this validator)."""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_PORT
    try:
        n = int(str(raw).strip())
    except ValueError:
        return None
    return n if 1024 <= n <= 65535 else None


def http_health(port: int, timeout: float = 2.0) -> dict | None:
    """GET http://127.0.0.1:<port>/api/health → parsed JSON or None."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        conn.request("GET", "/api/health", headers={"Host": f"127.0.0.1:{port}"})
        r = conn.getresponse()
        if r.status != 200:
            return None
        return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def is_our_app(port: int, timeout: float = 2.0) -> bool:
    """True when /api/health answers with *our* shape (status ok + python
    key — a foreign program holding the port won't return this)."""
    body = http_health(port, timeout)
    return (
        isinstance(body, dict)
        and body.get("status") == "ok"
        and body.get("python") is not None
    )


def port_free(port: int) -> bool:
    """Bind probe — the only race-free way to ask the OS."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def port_state(port: int) -> str:
    """``ours`` (our app already serves → just open the browser),
    ``busy`` (a foreign program owns the port), ``free``."""
    if is_our_app(port):
        return "ours"
    if port_free(port):
        return "free"
    return "busy"


def stale_build(root: Path) -> bool:
    """True when ``frontend/dist/index.html`` is missing or older than any
    tracked source (frontend/src/**, public/**, index.html,
    vite.config.ts, tsconfig*.json, package*.json)."""
    index = root / "frontend" / "dist" / "index.html"
    if not index.is_file():
        return True
    stamp = index.stat().st_mtime
    fe = root / "frontend"
    for d in ("src", "public"):
        p = fe / d
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and f.stat().st_mtime > stamp:
                    return True
    names = ("index.html", "vite.config.ts", "package.json",
             "package-lock.json")
    for f in list(names) + [p.name for p in fe.glob("tsconfig*.json")]:
        p = fe / f
        if p.is_file() and p.stat().st_mtime > stamp:
            return True
    return False


def need_uv_sync(root: Path) -> bool:
    """True when uv.lock/pyproject.toml changed after the venv was made."""
    marker = root / ".venv" / "pyvenv.cfg"
    if not marker.is_file():
        return True
    stamp = marker.stat().st_mtime
    for name in ("uv.lock", "pyproject.toml"):
        p = root / name
        if p.is_file() and p.stat().st_mtime > stamp:
            return True
    return False


def free_space_gb(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return round(usage.free / (1024 ** 3), 2)


def bundled_python(root: Path) -> Path | None:
    # ``python/Scripts/python.exe`` covers a Windows *venv* placed at
    # ``<app>/python`` — the ``safa-audio`` wheel provisions its runtime
    # exactly that way (M10d), so it must count as a bundled interpreter.
    for rel in ("python/python.exe", "python/Scripts/python.exe",
                "python/bin/python3", "python/bin/python"):
        p = root / rel
        if p.is_file():
            return p
    return None


def venv_python(root: Path) -> Path | None:
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python",
                ".venv/bin/python3"):
        p = root / rel
        if p.is_file():
            return p
    return None


def _ffmpeg_present(root: Path) -> bool:
    exe = ".exe" if os.name == "nt" else ""
    b = root / "tools" / "ffmpeg" / "bin"
    return (b / f"ffmpeg{exe}").is_file() and (b / f"ffprobe{exe}").is_file()


def _uv_extra() -> str:
    """Dev sync variant: SAFA_UV_EXTRA wins; cu124 on Windows/Linux dev,
    cpu on macOS (no CUDA wheels exist there)."""
    env = os.environ.get("SAFA_UV_EXTRA", "").strip()
    if env in ("cpu", "cu124"):
        return env
    return "cpu" if sys.platform == "darwin" else "cu124"


# ---- side-effecting steps ----------------------------------------------------


def _unblock_mark_of_the_web(root: Path) -> None:
    """Strip the Zone.Identifier stream Windows puts on files extracted
    from a downloaded zip — blocked DLLs fail to load (first start only,
    Windows only; ``data/.unblocked`` marks it done)."""
    marker = root / "data" / ".unblocked"
    if marker.is_file() or os.name != "nt":
        return
    bilingual("unblocking", color="cyan")
    marker.parent.mkdir(parents=True, exist_ok=True)
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                os.unlink(os.path.join(dirpath, name) + ":Zone.Identifier")
            except OSError:
                pass
    marker.touch()


def _prefetch_models(python: Path, root: Path) -> bool:
    script = root / "scripts" / "prefetch_models.py"
    if not script.is_file():
        return True  # nothing to run → not a failure
    bilingual("checking_models", color="cyan")
    try:
        rc = subprocess.run([str(python), str(script)], cwd=str(root)).returncode
    except OSError:
        rc = 1
    if rc != 0:
        bilingual("models_partial")
    return rc == 0


def _run_setup_repair(root: Path) -> bool:
    """Dev repair: setup.ps1 on Windows (uv + ffmpeg + npm), the same
    steps inline on POSIX (uv sync + ffmpeg presence warning)."""
    bilingual("setup_repair", color="cyan")
    setup_ps1 = root / "scripts" / "setup.ps1"
    if os.name == "nt" and setup_ps1.is_file():
        from backend.core.shortcut import windows_powershell_env

        rc = subprocess.run(
            [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(setup_ps1),
            ],
            cwd=str(root),
            env=windows_powershell_env(),
        ).returncode
        return rc == 0
    extra = _uv_extra()
    rc = subprocess.run(
        ["uv", "sync", "--extra", extra], cwd=str(root)
    ).returncode
    if rc == 0 and not _ffmpeg_present(root):
        bilingual("ffmpeg_missing")
    return rc == 0


def _server_env(port: int) -> dict:
    """Child env for the uvicorn process. The server's Host allow-list
    reads SOUND_SYSTEM_PORT — propagate the validated value so --port and
    the backend never disagree. It goes on the child's env only; writing
    os.environ here would leak the port into callers (and the whole test
    session)."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["SOUND_SYSTEM_PORT"] = str(port)
    return env


def _spawn_server(python: Path, root: Path, port: int) -> subprocess.Popen:
    env = _server_env(port)
    host = env.get("SOUND_SYSTEM_BIND_HOST", "").strip() or "127.0.0.1"
    return subprocess.Popen(
        [
            str(python), "-m", "uvicorn", "backend.main:app",
            "--host", host, "--port", str(port),
        ],
        cwd=str(root),
        env=env,
    )


def _wait_healthy(port: int, proc: subprocess.Popen,
                  timeout_s: float = HEALTH_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and proc.poll() is None:
        if is_our_app(port, timeout=2.0):
            return True
        time.sleep(0.5)
    return False


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill the server process tree; never raises."""
    try:
        if os.name == "nt":
            # taskkill /T kills the whole child tree, never orphans.
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:
        pass


def open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _first_run_shortcut() -> None:
    """First-run desktop shortcut (Windows only, dev/portable) — best
    effort, runs once, and can never delay or break startup."""
    try:
        from backend.core import shortcut

        if shortcut.maybe_create_desktop_shortcut(ROOT) == "created":
            bilingual("shortcut_created")
    except Exception:
        pass


# ---- start command ------------------------------------------------------------


def _find_python(root: Path) -> tuple[Path | None, bool, bool]:
    """(python, portable, broken_pkg): bundled python → portable package;
    .venv python → dev checkout; a python/ dir without an interpreter →
    broken package."""
    bp = bundled_python(root)
    if bp is not None:
        return bp, True, False
    vp = venv_python(root)
    if vp is not None:
        return vp, False, False
    py_dir = root / "python"
    return None, False, py_dir.is_dir()


def cmd_start(args) -> int:
    os.environ.setdefault("PYTHONUTF8", "1")
    raw_port = str(args.port) if args.port else os.environ.get(
        "SOUND_SYSTEM_PORT", "")
    port = valid_port(raw_port)
    if port is None:
        bilingual("bad_port", raw=raw_port)
        return 2
    base = f"http://127.0.0.1:{port}"

    # ---- warnings (non-fatal) ----
    root_str = str(ROOT)
    if "OneDrive" in root_str:
        bilingual("onedrive")
    if len(root_str) > LONG_PATH_CHARS:
        bilingual("longpath", n=len(root_str))
    try:
        gb = free_space_gb(ROOT)
        if gb < LOW_DISK_GB:
            bilingual("lowdisk", gb=gb)
    except Exception:
        pass

    # ---- single instance ----
    state = port_state(port)
    if args.check_only:
        print(f"STATE:{state}", flush=True)
        return 0
    if state == "ours":
        bilingual("already_running", color="cyan")
        if not args.no_browser:
            open_browser(base)
        return 0
    if state == "busy":
        bilingual("port_busy", port=port)
        return 3

    # ---- install mode ----
    python, portable, broken = _find_python(ROOT)
    if broken:
        bilingual("incomplete_pkg")
        return 4
    if python is None:
        # Dev checkout without a venv yet — bootstrap through uv below.
        python = None

    if portable:
        if not _ffmpeg_present(ROOT):
            bilingual("ffmpeg_missing")
            return 4
        _unblock_mark_of_the_web(ROOT)
        _prefetch_models(python, ROOT)
    else:
        # ---- dev repair ----
        if python is None or not _ffmpeg_present(ROOT):
            if not _run_setup_repair(ROOT):
                bilingual("setup_failed")
                return 4
            python = venv_python(ROOT)
            if python is None:
                bilingual("setup_failed")
                return 4
        if shutil.which("uv") is None:
            bilingual("uv_missing")
            return 5
        if need_uv_sync(ROOT):
            print(f"== uv sync --extra {_uv_extra()} ==", flush=True)
            rc = subprocess.run(
                ["uv", "sync", "--extra", _uv_extra()], cwd=str(ROOT)
            ).returncode
            if rc != 0:
                bilingual("uv_sync_failed")
                return 5
        if stale_build(ROOT):
            if shutil.which("npm") is None:
                bilingual("npm_missing")
                return 6
            print("== frontend build ==", flush=True)
            rc = subprocess.run(
                ["npm", "run", "build"],
                cwd=str(ROOT / "frontend"),
            ).returncode
            if rc != 0:
                bilingual("build_failed")
                return 6

    # ---- start ----
    proc = _spawn_server(python, ROOT, port)
    if not _wait_healthy(port, proc):
        _kill_tree(proc)
        bilingual("health_timeout", n=HEALTH_TIMEOUT_S)
        return 7
    _first_run_shortcut()
    if not args.no_browser:
        open_browser(base)

    # Foreground lifetime = server lifetime. Ctrl+C reaches uvicorn too →
    # its shutdown path cleans up workers; the C# launcher wraps the same
    # flow in a Job Object for hard-kill safety.
    try:
        return proc.wait()
    except KeyboardInterrupt:
        _kill_tree(proc)
        return 0
    finally:
        _kill_tree(proc)


# ---- models command ------------------------------------------------------------


def cmd_models(args) -> int:
    if args.models_action == "import":
        from backend.core.model_pack import import_pack

        return import_pack(
            Path(args.zip_path),
            models_dir=Path(args.models_dir) if args.models_dir else None,
            out=print,
        )
    return 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="safa", description=__doc__)
    p.add_argument("--port", type=int, default=0,
                   help="override SOUND_SYSTEM_PORT (validated 1024-65535)")
    p.add_argument("--no-browser", action="store_true",
                   help="don't open the browser")
    p.add_argument("--check-only", action="store_true",
                   help="print STATE:<ours|busy|free> and exit")
    sub = p.add_subparsers(dest="command")
    m = sub.add_parser("models", help="model pack operations")
    msub = m.add_subparsers(dest="models_action")
    imp = msub.add_parser("import", help="import an offline model pack zip")
    imp.add_argument("zip_path")
    imp.add_argument("--models-dir", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "models":
        return cmd_models(args)
    return cmd_start(args)


if __name__ == "__main__":
    sys.exit(main())
