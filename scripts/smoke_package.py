"""M10b: smoke-test a built portable package the way a friend gets it.

Extracts (or reuses) the package under an Arabic + spaces path, starts it
through ``start.bat`` with a PATH stripped of uv/node/python, then checks
/api/health + capabilities, runs a short Podcast job and fetches the
output file. The server is stopped at the end.

    uv run python scripts/smoke_package.py dist_pkg/Safa-0.1.0-win-x64-cpu-portable
    uv run python scripts/smoke_package.py dist_pkg/<name>.zip
        [--port 8795] [--models-dir DIR] [--keep] [--device cpu|gpu|auto]
        [--health-only]

An installed tree (``installed.json`` present, as the Inno setup writes)
is accepted without ``portable.txt``. ``--health-only`` stops after the
health + frontend checks (CI: no generated speech fixture there).

``--models-dir`` sets SOUND_SYSTEM_MODELS_DIR inside the packaged app so
the smoke run points at an existing models/ tree instead of downloading
~600 MB again.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTRACT_PARENT = ROOT / "data" / "tmp" / "pkg smoke" / "تجربة صوت"
FIXTURE = ROOT / "tests" / "fixtures" / "generated" / "speech.wav"
PODCAST_OPS = {
    "highpass": {},
    "dehum": {},
    "declick": {"mode": "mouth"},
    "denoise": {"engine": "ai", "strength_db": 24},
    "dereverb": {"strength": "gentle"},
    "breaths": {},
    "enhance": {},
    "silence": {"action": "remove"},
    "loudness": {"target": "podcast"},
}
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}

_REQUIRED = (
    "python/python.exe", "backend/main.py", "tools/ffmpeg/bin/ffmpeg.exe",
    "tools/ffmpeg/bin/ffprobe.exe", "frontend/dist/index.html",
    "start.bat", "scripts/start.ps1", "scripts/launcher-lib.ps1",
    "scripts/prefetch_models.py", "README.md", "README.ar.md",
    "Safa.exe", "portable.txt",
)


# ---- package location ------------------------------------------------------------


def package_dir(source: Path) -> Path:
    """Resolve a folder or .zip to an on-disk package root."""
    source = source.resolve()
    if source.is_dir():
        return source
    if source.suffix == ".zip" and source.is_file():
        return extract_package(source)
    raise FileNotFoundError(f"not a package dir or zip: {source}")


def extract_package(zip_path: Path, dest_parent: Path = EXTRACT_PARENT) -> Path:
    """Extract the zip under the Arabic + spaces dir; reuse a previous
    extraction when the marker records the same source (keeps reruns fast).
    """
    dest = dest_parent / zip_path.stem
    marker = dest / ".smoke_src"
    sig = f"{zip_path}|{zip_path.stat().st_size}|{int(zip_path.stat().st_mtime)}"
    if dest.is_dir() and marker.is_file() and marker.read_text() == sig:
        print(f"reusing extracted {dest}")
        return dest
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    t0 = time.monotonic()
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    # the zip nests one top folder with the same name as the zip stem
    inner = dest / zip_path.stem
    if inner.is_dir():
        for child in inner.iterdir():
            shutil.move(str(child), str(dest / child.name))
        inner.rmdir()
    marker.write_text(sig, encoding="utf-8")
    print(f"extracted {zip_path.name} -> {dest} ({time.monotonic()-t0:.0f}s)")
    return dest


# ---- environment ----------------------------------------------------------------


def stripped_env(port: int, models_dir: Path | None) -> dict:
    """A normal user env with PATH reduced to Windows system dirs — no
    uv/node/python/git resolves (powershell.exe, taskkill, nvidia-smi do;
    System32 ships nvidia-smi). Keep the rest of the env: NVML/driver init
    needs %ProgramFiles%, so a whitelist env is stricter than a real
    friend's machine and makes gpu_info() falsely report no GPU.
    PYTHONPATH/PYTHONHOME/VIRTUAL_ENV are dropped so the dev interpreter
    can't leak into the bundled one."""
    win = os.environ.get("SystemRoot", r"C:\Windows")
    keep = [
        win + r"\System32",
        win,
        win + r"\System32\Wbem",
        win + r"\System32\WindowsPowerShell\v1.0",
    ]
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    env["PATH"] = ";".join(keep)
    env["SOUND_SYSTEM_PORT"] = str(port)
    env["PYTHONUTF8"] = "1"
    if models_dir is not None:
        env["SOUND_SYSTEM_MODELS_DIR"] = str(models_dir)
    return env


# ---- http helpers -----------------------------------------------------------------


def _req(base: str, path: str, **kw) -> urllib.request.Request:
    return urllib.request.Request(base + path, **kw)


def _json(resp) -> dict:
    return json.loads(resp.read().decode("utf-8"))


def wait_health(base: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    last = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                _req(base, "/api/health"), timeout=3
            ) as r:
                body = _json(r)
                if body.get("status") == "ok":
                    return body
                last = f"unexpected body {body!r}"
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(1.0)
    raise TimeoutError(f"/api/health never answered ok ({last})")


def run_podcast_job(
    base: str, wav: Path, device: str, timeout_s: float
) -> dict:
    """Upload ``wav``, run the Podcast preset, wait, return the job payload."""
    media = wav.read_bytes()
    req = _req(
        base,
        "/api/media",
        data=media,
        headers={
            "X-Filename": "speech.wav",
            "Content-Type": "application/octet-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        media_id = _json(r)["media_id"]

    req = _req(
        base,
        "/api/jobs",
        data=json.dumps(
            {
                "media_id": media_id,
                "device": device,
                "ops": PODCAST_OPS,
                "output": {"audio_format": "wav", "keep_video": False},
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            job = _json(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"POST /api/jobs -> {e.code} {e.read()!r}")
    job_id = job["id"]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with urllib.request.urlopen(
            _req(base, f"/api/jobs/{job_id}"), timeout=10
        ) as r:
            job = _json(r)
        if job["state"] in TERMINAL:
            return job
        time.sleep(2.0)
    raise TimeoutError(f"job {job_id} still {job['state']}")


def fetch_output(base: str, job: dict, dest_dir: Path) -> Path:
    audio = [f for f in job["files"] if f["kind"] == "audio"]
    if not audio:
        raise RuntimeError(f"no audio output in job files: {job['files']}")
    name = audio[0]["name"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / name
    with urllib.request.urlopen(
        _req(base, f"/api/jobs/{job['id']}/files/{name}"), timeout=60
    ) as r, out.open("wb") as fh:
        shutil.copyfileobj(r, fh)
    return out


# ---- process control --------------------------------------------------------------


def start_server(pkg_root: Path, port: int, env: dict, log_path: Path):
    log = log_path.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [str(pkg_root / "start.bat"), "--port", str(port), "--no-browser"],
        cwd=str(pkg_root),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    return proc, log


def stop_server(proc, port: int) -> None:
    if proc.poll() is None:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
        )
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", port))
            s.close()
            return
        except OSError:
            time.sleep(0.5)


# ---- smoke ------------------------------------------------------------------------


def check_layout(pkg_root: Path) -> list[str]:
    # installers strip portable.txt on purpose and write installed.json
    installed = (pkg_root / "installed.json").is_file()
    return [p for p in _REQUIRED
            if not (installed and p == "portable.txt")
            and not (pkg_root / p).is_file()]


def smoke(
    pkg_root: Path,
    port: int,
    models_dir: Path | None,
    device: str = "auto",
    keep: bool = False,
    health_only: bool = False,
) -> dict:
    pkg_root = pkg_root.resolve()
    missing = check_layout(pkg_root)
    if missing:
        raise RuntimeError(f"package layout incomplete: {missing}")
    variant = "gpu" if "-gpu" in pkg_root.name else "cpu"
    base = f"http://127.0.0.1:{port}"
    log_path = pkg_root / "data" / "smoke-start.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = stripped_env(port, models_dir)
    proc, log = start_server(pkg_root, port, env, log_path)
    t0 = time.monotonic()
    result: dict = {"package": pkg_root.name, "variant": variant, "port": port}
    try:
        health = wait_health(base, 300.0)
        result["health_s"] = round(time.monotonic() - t0, 1)
        result["health"] = {
            "python": health["python"],
            "device": health["device"],
            "cuda_dlls": health["cuda_dlls"],
            "gpu": health.get("gpu"),
            "modules_ok": all(health["modules"].values()),
            "models": health["models"],
            "ffmpeg_version": health["ffmpeg"].get("version"),
        }
        assert health["python"].startswith("3.11"), health["python"]
        assert all(health["modules"].values()), health["modules"]
        if variant == "cpu":
            assert health["cuda_dlls"] is False, "cpu build reports CUDA"
            assert health["device"] == "cpu"
        else:
            assert health["cuda_dlls"] is True, "gpu build lacks CUDA dlls"
        # frontend serving
        with urllib.request.urlopen(_req(base, "/"), timeout=10) as r:
            assert "text/html" in r.headers.get("Content-Type", "")
        if health_only:
            result["ok"] = True
            return result
        # job device: auto follows what the app itself reports
        job_device = device
        if job_device == "auto":
            job_device = "gpu" if health["device"] == "gpu" else "cpu"
        result["job_device"] = job_device
        t1 = time.monotonic()
        job = run_podcast_job(base, FIXTURE, job_device, 900.0)
        result["job_s"] = round(time.monotonic() - t1, 1)
        if job["state"] != "completed":
            raise RuntimeError(f"job ended as {job['state']}: {job}")
        out = fetch_output(base, job, pkg_root / "data" / "smoke_out")
        result["output"] = {"name": out.name, "bytes": out.stat().st_size}
        assert out.stat().st_size > 10_000
        result["ok"] = True
    finally:
        log.close()
        if not keep:
            stop_server(proc, port)
    return result


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package", type=Path, help="package folder or .zip")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--models-dir", type=Path, default=ROOT / "models")
    ap.add_argument("--device", choices=["auto", "cpu", "gpu"], default="auto")
    ap.add_argument("--keep-running", dest="keep", action="store_true",
                    help="leave the server up for manual poking")
    ap.add_argument("--health-only", action="store_true",
                    help="stop after health + frontend checks (no job)")
    args = ap.parse_args(argv)
    models_dir = args.models_dir
    if models_dir is not None and not models_dir.is_absolute():
        # A drive-relative value like ``F:models`` (backslashes eaten by
        # an unquoted bash arg) resolves inside the package — models then
        # re-download into <pkg>/sound_systemmodels. Fail loudly instead.
        ap.error(
            "--models-dir must be absolute "
            "(quote the path; forward slashes work too): "
            f"got {args.models_dir!s}"
        )
    pkg = package_dir(args.package)
    res = smoke(pkg, args.port, models_dir, args.device, args.keep,
                args.health_only)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
