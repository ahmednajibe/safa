"""``safa`` — bootstrap CLI shipped by the ``safa-audio`` wheel (M10d).

Stdlib-only by design: this code runs before the heavy runtime exists.

  safa setup --device auto|cpu|cuda [--data-dir D] [--ffmpeg-dir D]
             [--reinstall] [--use-pip]
  safa [start] [--port N] [--no-browser] [--check-only]
  safa models import <pack.zip>

``setup`` stages the app payload the wheel carries into
``<data>/runtime/app`` and provisions the heavy runtime there:

  * python: a private Python 3.11 under ``<app>/python`` — a uv-managed
    standalone copied in (identical to scripts/install-runtime.ps1) when
    ``uv`` is on PATH, else a venv made with the running interpreter
    (must then itself be 3.11).
  * deps:   ``requirements-<variant>.txt`` — the frozen, hashed lock the
    wheel carries — installed via ``uv pip`` or ``pip`` with
    ``--require-hashes`` and the matching PyTorch index
    (cpu → download.pytorch.org/whl/cpu, cuda → .../cu124).
  * ffmpeg: copied from ``--ffmpeg-dir`` or PATH when present, else the
    pinned static build below is downloaded and sha256-verified.

Data never lives in site-packages: ``<data>`` is
``SOUND_SYSTEM_DATA_DIR`` or the platform user-data dir
(%LOCALAPPDATA%\\Safa · ~/Library/Application Support/Safa ·
${XDG_DATA_HOME:-~/.local/share}/safa-data). ``installed.json`` inside
the app dir records it — the same installed-mode contract the Inno
installer and install.sh use (backend/core/paths.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

PAYLOAD = Path(__file__).resolve().parent / "app"
"""App tree staged inside the wheel by scripts/build-wheel.py."""

_RUNTIME = Path("runtime") / "app"
_TORCH_INDEX = {
    "cpu": "https://download.pytorch.org/whl/cpu",
    "cu124": "https://download.pytorch.org/whl/cu124",
}
_VARIANT_EXTRA = {"cpu": "cpu", "cuda": "cu124"}

# Pinned static ffmpeg builds for the download fallback. URLs are
# versioned (never moving targets); sha256 was computed from the real
# artifacts. Bump both together. macOS has no trustworthy pinned arm64
# static build — there we require ffmpeg on PATH (brew install ffmpeg).
_FFMPEG_DL = {
    "win32": (
        "https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/"
        "ffmpeg-7.1.1-essentials_build.zip",
        "04861d3339c5ebe38b56c19a15cf2c0cc97f5de4fa8910e4d47e5e6404e4a2d4",
        "zip",
    ),
    "linux": (
        "https://www.johnvansickle.com/ffmpeg/old-releases/"
        "ffmpeg-6.0.1-amd64-static.tar.xz",
        "28268bf402f1083833ea269331587f60a242848880073be8016501d864bd07a5",
        "tar.xz",
    ),
}

_STR = {
    "no_311": (
        "Python 3.11 is required to provision without uv. Install uv "
        "(https://docs.astral.sh/uv) or Python 3.11, then re-run setup.",
        "يلزم بايثون 3.11 للتثبيت بدون uv. ثبّت uv أو بايثون 3.11 ثم أعد المحاولة.",
    ),
    "ffmpeg_missing": (
        "ffmpeg not found on PATH and no static build exists for this "
        "platform. Install ffmpeg and re-run `safa setup`, or pass "
        "--ffmpeg-dir <dir-with-ffmpeg+ffprobe>.",
        "لم يُعثر على ffmpeg في PATH ولا توجد نسخة ثابتة لهذه المنصة. ثبّت "
        "ffmpeg وأعد `safa setup` أو مرّر --ffmpeg-dir.",
    ),
    "no_runtime": (
        "Runtime not provisioned yet — run `safa setup` first.",
        "بيئة التشغيل غير جاهزة بعد — شغّل `safa setup` أولاً.",
    ),
    "downloading": (
        "Downloading {what} ({mb} MB, sha256-verified)…",
        "يتم تنزيل {what} ({mb} م.ب، ببصمة sha256)…",
    ),
    "hash_bad": (
        "Checksum mismatch for {what} — download deleted, retry setup.",
        "عدم تطابق البصمة لـ {what} — حُذف التنزيل، أعد الإعداد.",
    ),
}


def _say(key: str, **kw) -> None:
    en, ar = _STR[key]
    print(en.format(**kw), flush=True)
    print(ar.format(**kw), flush=True)


# ---- locations ------------------------------------------------------------------


def data_dir() -> Path:
    """User-data root: ``SOUND_SYSTEM_DATA_DIR`` wins, else the platform
    default (never site-packages)."""
    env = os.environ.get("SOUND_SYSTEM_DATA_DIR", "").strip()
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local")
        return Path(base) / "Safa"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Safa"
    base = os.environ.get("XDG_DATA_HOME") or str(
        Path.home() / ".local" / "share")
    return Path(base) / "safa-data"


def app_dir(data: Path) -> Path:
    return Path(data) / _RUNTIME


def release_info() -> dict:
    """release-info.toml staged inside the payload (app + port)."""
    try:
        return tomllib.loads(
            (PAYLOAD / "release-info.toml").read_text("utf-8"))
    except Exception:
        return {}


def _port() -> int:
    try:
        return int(release_info()["app"]["port"])
    except Exception:
        return 8790


# ---- device / variant ------------------------------------------------------------


def detect_nvidia() -> bool:
    """nvidia-smi + driver >= driver_min → the cu124 variant makes sense."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return False
    try:
        out = subprocess.run(
            [smi, "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15,
        )
        major = int(out.stdout.strip().splitlines()[0].split(".")[0])
        minimum = int(release_info().get("app", {}).get("driver_min", 551))
        return major >= minimum
    except Exception:
        return False


def resolve_device(choice: str) -> str:
    """auto|cpu|cuda → cpu|cu124. CUDA never applies to macOS/aarch64."""
    if choice == "cpu":
        return "cpu"
    if choice == "auto":
        if sys.platform == "darwin" or detect_nvidia() is False:
            return "cpu"
        return "cu124"
    # explicit cuda
    if sys.platform == "darwin":
        print("CUDA is not available on macOS — using the CPU variant.",
              flush=True)
        return "cpu"
    return "cu124"


# ---- staging -------------------------------------------------------------------


def _clean_copy(src: Path, dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_tmp")
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore)


def stage_payload(app: Path) -> None:
    """Copy the wheel's app payload into ``<data>/runtime/app``."""
    if not (PAYLOAD / "backend").is_dir():
        raise SystemExit(
            f"wheel payload missing at {PAYLOAD} — reinstall safa-audio")
    _clean_copy(PAYLOAD, app)


def _app_python(app: Path) -> Path | None:
    for rel in ("python/python.exe", "python/Scripts/python.exe",
                "python/bin/python3", "python/bin/python"):
        p = app / rel
        if p.is_file():
            return p
    return None


def write_installed_json(app: Path, data: Path, variant: str) -> None:
    marker = {
        "data_dir": str(Path(data).resolve()),
        "app_version": _version(),
        "variant": variant,
    }
    (app / "installed.json").write_text(
        json.dumps(marker, ensure_ascii=True) + "\n", encoding="ascii")


def _version() -> str:
    try:
        from safa_bootstrap import __version__
        return __version__
    except Exception:
        return "0.0.0"


# ---- runtime provisioning -----------------------------------------------------------


def _uv() -> str | None:
    return shutil.which("uv")


def _install_python_uv(app: Path, data: Path) -> None:
    """uv-managed standalone 3.11 copied to <app>/python — the same shape
    scripts/install-runtime.ps1 produces for the Inno installers."""
    uv = _uv()
    pyroot = app / ".pyroot-tmp"
    shutil.rmtree(pyroot, ignore_errors=True)
    subprocess.run(
        [uv, "python", "install", "3.11", "--install-dir", str(pyroot)],
        check=True)
    srcs = sorted(pyroot.glob("cpython-3.11.*"))
    if not srcs:
        raise RuntimeError(f"no cpython-3.11 tree under {pyroot}")
    target = app / "python"
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(srcs[0], target)
    shutil.rmtree(pyroot, ignore_errors=True)
    (target / "Lib" / "EXTERNALLY-MANAGED").unlink(missing_ok=True)


def _install_python_venv(app: Path) -> None:
    if sys.version_info[:2] != (3, 11):
        _say("no_311")
        raise SystemExit(5)
    subprocess.run(
        [sys.executable, "-m", "venv", str(app / "python")], check=True)


def install_deps(app: Path, variant: str, uv: str | None) -> None:
    """Install the frozen hashed lock for ``variant`` into <app>/python."""
    req = app / f"requirements-{variant}.txt"
    py = _app_python(app)
    if py is None:
        raise RuntimeError(f"no interpreter provisioned under {app}/python")
    index = _TORCH_INDEX[variant]
    if uv:
        subprocess.run(
            # --no-config: a pyproject.toml nearby must not apply its
            # [tool.uv] override (it drops onnxruntime) to the export.
            # --no-deps: the export is the full locked closure; re-resolving
            # trips over faster-whisper's `onnxruntime` vs onnxruntime-gpu.
            [uv, "pip", "install", "--no-config", "--no-deps", "--python", str(py), "--system",
             "-r", str(req), "--require-hashes",
             "--extra-index-url", index,
             "--index-strategy", "unsafe-best-match"],
            check=True)
    else:
        subprocess.run(
            [str(py), "-m", "pip", "install", "-r", str(req), "--no-deps",
             "--require-hashes", "--extra-index-url", index],
            check=True)


# ---- ffmpeg -----------------------------------------------------------------------


def _ffmpeg_bin(app: Path) -> Path:
    return app / "tools" / "ffmpeg" / "bin"


def _ffmpeg_present(app: Path) -> bool:
    exe = ".exe" if sys.platform == "win32" else ""
    b = _ffmpeg_bin(app)
    return (b / f"ffmpeg{exe}").is_file() and (
        b / f"ffprobe{exe}").is_file()


def _copy_ffmpeg(src_dir: Path, app: Path) -> bool:
    exe = ".exe" if sys.platform == "win32" else ""
    src_dir = Path(src_dir)
    ff, fp = src_dir / f"ffmpeg{exe}", src_dir / f"ffprobe{exe}"
    if not (ff.is_file() and fp.is_file()):
        return False
    dst = _ffmpeg_bin(app)
    dst.mkdir(parents=True, exist_ok=True)
    for f in (ff, fp):
        out = dst / f.name
        shutil.copy2(f, out)
        if sys.platform != "win32":
            out.chmod(0o755)
    return True


def _download(url: str, dst: Path, sha256: str, what: str) -> None:
    _say("downloading", what=what, mb="~90")
    tmp = dst.with_suffix(dst.suffix + ".part")
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=120) as r, tmp.open(
                "wb") as f:
            while chunk := r.read(1 << 20):
                h.update(chunk)
                f.write(chunk)
        if h.hexdigest() == sha256:
            tmp.replace(dst)
            return
        tmp.unlink(missing_ok=True)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    _say("hash_bad", what=what)
    raise SystemExit(1)


def _extract_ffmpeg_archive(archive: Path, kind: str, app: Path) -> bool:
    dst = _ffmpeg_bin(app)
    dst.mkdir(parents=True, exist_ok=True)
    exe = ".exe" if sys.platform == "win32" else ""
    names = {f"ffmpeg{exe}", f"ffprobe{exe}"}
    if kind == "zip":
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                if Path(member).name in names and member.endswith(exe):
                    with z.open(member) as r, (dst / Path(member).name
                                               ).open("wb") as w:
                        shutil.copyfileobj(r, w)
    else:
        with tarfile.open(archive, "r:xz") as t:
            for member in t.getmembers():
                if Path(member.name).name in names and member.isfile():
                    src = t.extractfile(member)
                    if src is None:
                        continue
                    with (dst / Path(member.name).name).open("wb") as w:
                        shutil.copyfileobj(src, w)
    for f in dst.iterdir():
        if sys.platform != "win32":
            f.chmod(0o755)
    return _ffmpeg_present(app)


def provision_ffmpeg(app: Path, ffmpeg_dir: str | None) -> None:
    """Guarantee <app>/tools/ffmpeg/bin/{ffmpeg,ffprobe}: --ffmpeg-dir →
    PATH → pinned static download (sha256-verified)."""
    if _ffmpeg_present(app):
        return
    if ffmpeg_dir and _copy_ffmpeg(Path(ffmpeg_dir), app):
        return
    ff = shutil.which("ffmpeg")
    if ff and _copy_ffmpeg(Path(ff).parent, app):
        return
    dl = _FFMPEG_DL.get(sys.platform)
    if dl is None:
        _say("ffmpeg_missing")
        raise SystemExit(4)
    url, sha, kind = dl
    tmp_root = app.parents[1] / "tmp"  # <data>/tmp — app drive, not %TEMP%
    tmp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
            dir=str(tmp_root), prefix="ffmpeg-dl-") as td:
        arc = Path(td) / ("ffmpeg." + kind)
        _download(url, arc, sha, "ffmpeg")
        if not _extract_ffmpeg_archive(arc, kind, app):
            _say("ffmpeg_missing")
            raise SystemExit(4)


# ---- commands ------------------------------------------------------------------


def cmd_setup(args) -> int:
    data = Path(args.data_dir).expanduser() if args.data_dir else data_dir()
    data.mkdir(parents=True, exist_ok=True)
    app = app_dir(data)
    variant = resolve_device(args.device)

    if args.reinstall and app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True, exist_ok=True)

    print(f"== safa setup: data={data} variant={variant} ==", flush=True)
    stage_payload(app)

    if _app_python(app) is None:
        uv = None if args.use_pip else _uv()
        if uv:
            _install_python_uv(app, data)
        else:
            if not args.use_pip:
                print("uv not on PATH — falling back to venv+pip.",
                      flush=True)
            _install_python_venv(app)

    install_deps(app, variant, None if args.use_pip else _uv())
    provision_ffmpeg(app, args.ffmpeg_dir)
    write_installed_json(app, data, variant)
    print(f"== done — start with `safa` (http://127.0.0.1:{_port()}) ==",
          flush=True)
    return 0


def cmd_start(passthrough: list[str]) -> int:
    app = app_dir(data_dir())
    py = _app_python(app)
    if py is None:
        _say("no_runtime")
        return 4
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("SOUND_SYSTEM_DATA_DIR", str(data_dir()))
    proc = subprocess.Popen(
        [str(py), "-m", "backend.launcher", *passthrough],
        cwd=str(app), env=env)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            proc.terminate()
        return 0


def cmd_models(args) -> int:
    data = data_dir()
    app = app_dir(data)
    py = _app_python(app)
    if py is not None:
        cmd = [str(py), "-m", "backend.launcher", "models", "import",
               args.zip_path, "--models-dir", str(data / "models")]
        return subprocess.run(cmd, cwd=str(app)).returncode
    # pre-setup import: the model-pack code is stdlib-only, run it from
    # the wheel's own payload (the runtime app dir may not exist yet).
    os.environ.setdefault("SOUND_SYSTEM_MODELS_DIR", str(data / "models"))
    sys.path.insert(0, str(PAYLOAD))
    from backend.core.model_pack import import_pack  # noqa: E402
    return import_pack(Path(args.zip_path),
                       models_dir=data / "models", out=print)


def _build_setup_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="safa setup")
    p.add_argument("--device", choices=["auto", "cpu", "cuda"],
                   default="auto")
    p.add_argument("--data-dir", default=None,
                   help="user data dir (default: platform user-data dir; "
                        "SOUND_SYSTEM_DATA_DIR wins)")
    p.add_argument("--ffmpeg-dir", default=None,
                   help="dir containing ffmpeg+ffprobe to copy in")
    p.add_argument("--use-pip", action="store_true",
                   help="force venv+pip even when uv is on PATH")
    p.add_argument("--reinstall", action="store_true",
                   help="wipe <data>/runtime/app and provision fresh")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "start"
    if cmd == "setup":
        return cmd_setup(_build_setup_parser().parse_args(argv[1:]))
    if cmd == "models":
        p = argparse.ArgumentParser(prog="safa models")
        p.add_argument("action", choices=["import"])
        p.add_argument("zip_path")
        return cmd_models(p.parse_args(argv[1:]))
    # `safa`, `safa start`, and every launcher flag pass straight through.
    if cmd == "start":
        passthrough = argv[1:]
    else:
        passthrough = argv
    return cmd_start(passthrough)


if __name__ == "__main__":
    sys.exit(main())
