"""install.ps1 / install.sh / docker-compose contract tests (M10c).

- install.ps1 supports -Variant/-DataDir/-InstallDir/-Offline/-DryRun
  (dry-run mode verified by execution).
- install.sh lints under `sh -n`, offers --variant/--dir/--data-dir,
  stays POSIX, only touches our own dirs.
- docker-compose.yml pins host binding to 127.0.0.1:8790:8790, mounts
  /data, and sets SOUND_SYSTEM_DATA_DIR=/data.
- Manifests (winget/scoop/brew/AUR) reference the canonical asset names.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---- install.ps1 ----------------------------------------------------------------

PS = shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(PS is None, reason="powershell not on PATH")
def test_install_ps1_dry_run():
    out = subprocess.run(
        [PS, "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(ROOT / "install.ps1"), "-DryRun"],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stderr
    assert "[dry-run]" in out.stdout


def test_install_ps1_params_and_placeholder():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")
    for p in ("$Variant", "$DataDir", "$InstallDir", "$Offline", "$DryRun"):
        assert p in text, f"install.ps1 lacks {p}"
    assert "github.com/ahmednajibe/safa" in text
    # installs per-user, never Program Files
    assert "LOCALAPPDATA" in text


def test_install_runtime_requires_hashes():
    """uv pip install must never silently accept an unhashed requirements
    file — the frozen exports carry hashes and --require-hashes enforces
    them (a hashless file fails loud instead of installing unchecked)."""
    text = (ROOT / "scripts" / "install-runtime.ps1").read_text(
        encoding="utf-8-sig")
    assert "--require-hashes" in text


def test_ps1_nonascii_files_have_utf8_bom():
    """PS 5.1 parses a BOM-less .ps1 as ANSI — a non-ASCII char (Arabic,
    em-dash) then mojibakes and can corrupt later string tokens
    (install-runtime.ps1 broke exactly this way)."""
    bad = []
    for p in list(ROOT.glob("*.ps1")) + list((ROOT / "scripts").glob("*.ps1")):
        data = p.read_bytes()
        if any(b > 127 for b in data) and not data.startswith(b"\xef\xbb\xbf"):
            bad.append(p.name)
    assert not bad, f"non-ASCII .ps1 without UTF-8 BOM: {bad}"


# ---- install.sh ------------------------------------------------------------------

def _bash_sh() -> str | None:
    return shutil.which("bash") or shutil.which("sh")


def test_install_sh_lint():
    sh = _bash_sh()
    if sh is None:
        pytest.skip("no sh/bash on PATH")
    rc = subprocess.run(
        [sh, "-n", str(ROOT / "install.sh")],
        capture_output=True, text=True, timeout=60,
    )
    assert rc.returncode == 0, rc.stderr


def test_install_sh_dry_run():
    sh = _bash_sh()
    if sh is None:
        pytest.skip("no sh/bash on PATH")
    out = subprocess.run(
        [sh, str(ROOT / "install.sh"), "--dry-run"],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stderr
    assert "[dry-run]" in out.stdout


# ---- install.ps1: thin verified wrapper around setup.exe (M10c review) --------


INSTALL_PS1 = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")


def test_install_ps1_static_safety():
    """install.ps1 must be a thin verified wrapper around setup.exe:
    Get-FileHash verification, no recursive wipe of $InstallDir, no
    uv download, no source-archive provisioning."""
    assert "Get-FileHash" in INSTALL_PS1
    # the deadly line was Remove-Item -Recurse on $InstallDir children
    for m in re.finditer(r"Remove-Item[^\n|]*", INSTALL_PS1):
        frag = m.group(0)
        if "-Recurse" in frag:
            assert "$InstallDir" not in frag, frag
    assert "releases/latest/download/uv-" not in INSTALL_PS1
    assert "install-runtime" not in INSTALL_PS1
    assert "source.zip" not in INSTALL_PS1 and "git archive" not in INSTALL_PS1
    for token in ("-SetupExe", "/VERYSILENT", "/SUPPRESSMSGBOXES",
                  "/NORESTART", "/CURRENTUSER", "/VARIANT=", "/DATADIR=",
                  "/LOG=", "SHA256SUMS.txt", "Safa-install-logs"):
        assert token in INSTALL_PS1, token


def _ps1_function(name: str) -> str:
    """Extract a top-level `function <name> { ... }` from install.ps1 by
    brace matching, so the SHIPPED code is what the test runs."""
    src = INSTALL_PS1
    i = src.index(f"function {name}")
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError(f"unbalanced braces in function {name}")


def _ps_run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
        errors="replace",
    )


@pytest.fixture
def checksum_lib(tmp_path):
    """Dot-sourceable lib containing the install.ps1 checksum helpers."""
    lib = tmp_path / "checksum-lib.ps1"
    lib.write_text(
        _ps1_function("Test-SafaSha256") + "\n"
        + _ps1_function("Get-SafaSha256Entry"),
        encoding="utf-8-sig")
    return lib


def test_install_ps1_checksum_function_good_and_bad(tmp_path, checksum_lib):
    import hashlib
    f = tmp_path / "setup.exe"
    f.write_bytes(b"safa-setup")
    good = hashlib.sha256(b"safa-setup").hexdigest()
    lib = str(checksum_lib)
    r = _ps_run(
        f". '{lib}'; if (Test-SafaSha256 '{f}' '{good}') {{ 'ok' }}"
        " else { 'bad'; exit 1 }")
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr
    r = _ps_run(
        f". '{lib}'; if (Test-SafaSha256 '{f}' '{'0' * 64}') {{ 'bad'; "
        f"exit 1 }} else {{ 'ok' }}")
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr


def test_install_ps1_sums_entry_lookup(tmp_path, checksum_lib):
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(
        "aaaa  Safa-0.1.0-win-x64-setup.exe\n"
        "bbbb  ./Safa-0.1.0-win-x64-cpu-offline-setup.exe\n"
        "cccc  *Safa-0.1.0-win-x64-gpu-offline-setup.exe\n",
        encoding="utf-8")
    lib = str(checksum_lib)
    r = _ps_run(
        f". '{lib}'; "
        f"Get-SafaSha256Entry '{sums}' 'Safa-0.1.0-win-x64-setup.exe'; "
        f"Get-SafaSha256Entry '{sums}' "
        f"'Safa-0.1.0-win-x64-cpu-offline-setup.exe'; "
        f"Get-SafaSha256Entry '{sums}' "
        f"'Safa-0.1.0-win-x64-gpu-offline-setup.exe'; "
        f"$m = Get-SafaSha256Entry '{sums}' 'nope.exe'; "
        f"if ($null -eq $m) {{ 'null-ok' }} else {{ 'null-bad'; exit 1 }}")
    assert r.returncode == 0, r.stderr
    assert "aaaa" in r.stdout and "bbbb" in r.stdout and "cccc" in r.stdout
    assert "null-ok" in r.stdout


def test_install_ps1_dry_run_offline(tmp_path):
    setup = tmp_path / "Safa-0.1.0-win-x64-setup.exe"
    setup.write_bytes(b"x")
    out = _ps_run(
        f"& '{ROOT / 'install.ps1'}' -Offline -SetupExe '{setup}' "
        f"-InstallDir '{tmp_path / 'app dir'}' "
        f"-DataDir '{tmp_path / 'data تجربة'}' -Variant cpu -DryRun")
    assert out.returncode == 0, out.stderr
    assert "[dry-run]" in out.stdout
    assert "Safa-0.1.0-win-x64-setup.exe" in out.stdout


def test_install_sh_options_and_safety():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    for opt in ("--variant", "--dir", "--data-dir", "--dry-run",
                "--from-dir"):
        assert opt in text
    # quarantine limited to the app dir itself
    assert 'com.apple.quarantine "$DIR"' in text
    assert "Applications/Safa" in text and ".local/share/safa" in text


def test_install_sh_verifies_checksums():
    """The tarball is verified against SHA256SUMS.txt before extract —
    mandatory (sha256sum or shasum -a 256), bilingual error, exit 1."""
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "sha256sum" in text or "shasum" in text
    # the sums download must not be allowed to fail silently
    assert "SHA256SUMS.txt" in text
    dl = text[text.index("== download =="):text.index("== verify ==")]
    assert "|| true" not in dl
    # verify happens BEFORE extract
    assert text.index("== verify ==") < text.index("== extract ==")


# ---- real local verify (Git Bash / any POSIX sh) ----------------------------------


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _fake_release(dir_: Path, ver: str = "1.2.3",
                  key: str = "linux-x64-cpu",
                  portable: bool = False) -> Path:
    """Fake release dir: tarball with one inner folder + a marker file."""
    import tarfile
    name = f"Safa-{ver}-{key}"
    inner = dir_ / "tree" / name
    inner.mkdir(parents=True)
    (inner / "backend").mkdir()
    (inner / "backend" / "main.py").write_text("# safa\n")
    (inner / "VERSION.txt").write_text(ver + "\n")
    if portable:
        (inner / "portable.txt").write_text("portable\n")
    tb = dir_ / f"{name}.tar.gz"
    with tarfile.open(tb, "w:gz") as t:
        t.add(inner, arcname=name)
    return tb


def _write_sums(dir_: Path, tb: Path, name: str | None = None,
                corrupt: bool = False):
    h = _sha256(tb.read_bytes())
    if corrupt:
        h = "0" * 64
    (dir_ / "SHA256SUMS.txt").write_text(
        f"{h}  {name or tb.name}\n", encoding="utf-8")


def _run_install_sh(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    sh = _bash_sh()
    if sh is None:
        pytest.skip("no sh/bash on PATH")
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")  # never touch the real profile
    return subprocess.run(
        [sh, str(ROOT / "install.sh"), *args],
        capture_output=True, text=True, timeout=120, encoding="utf-8",
        errors="replace", env=env,
    )


@pytest.mark.skipif(
    not (shutil.which("sha256sum") or shutil.which("shasum")),
    reason="no sha256 tool")
def test_install_sh_from_dir_good_sum(tmp_path):
    rel = tmp_path / "rel"
    rel.mkdir()
    tb = _fake_release(rel)
    _write_sums(rel, tb)
    app, data = tmp_path / "app dir", tmp_path / "data dir"
    r = _run_install_sh(
        tmp_path, "--from-dir", str(rel), "--version", "1.2.3",
        "--dir", str(app), "--data-dir", str(data))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (app / "backend" / "main.py").is_file()
    assert json.loads((app / "installed.json").read_text())[
        "data_dir"] == str(data)


@pytest.mark.skipif(
    not (shutil.which("sha256sum") or shutil.which("shasum")),
    reason="no sha256 tool")
def test_install_sh_from_dir_bad_sum_refuses(tmp_path):
    rel = tmp_path / "rel"
    rel.mkdir()
    tb = _fake_release(rel)
    _write_sums(rel, tb, corrupt=True)
    app = tmp_path / "app"
    r = _run_install_sh(
        tmp_path, "--from-dir", str(rel), "--version", "1.2.3",
        "--dir", str(app), "--data-dir", str(tmp_path / "data"))
    assert r.returncode == 1
    assert not (app / "backend" / "main.py").exists()  # refused pre-extract


@pytest.mark.skipif(
    not (shutil.which("sha256sum") or shutil.which("shasum")),
    reason="no sha256 tool")
def test_install_sh_from_dir_missing_sums_refuses(tmp_path):
    rel = tmp_path / "rel"
    rel.mkdir()
    _fake_release(rel)
    r = _run_install_sh(
        tmp_path, "--from-dir", str(rel), "--version", "1.2.3",
        "--dir", str(tmp_path / "app"), "--data-dir", str(tmp_path / "d"))
    assert r.returncode == 1


@pytest.mark.skipif(
    not (shutil.which("sha256sum") or shutil.which("shasum")),
    reason="no sha256 tool")
def test_install_sh_from_dir_missing_entry_refuses(tmp_path):
    rel = tmp_path / "rel"
    rel.mkdir()
    tb = _fake_release(rel)
    _write_sums(rel, tb, name="Safa-other-asset.zip")
    r = _run_install_sh(
        tmp_path, "--from-dir", str(rel), "--version", "1.2.3",
        "--dir", str(tmp_path / "app"), "--data-dir", str(tmp_path / "d"))
    assert r.returncode == 1


@pytest.mark.skipif(
    not (shutil.which("sha256sum") or shutil.which("shasum")),
    reason="no sha256 tool")
def test_install_sh_strips_portable_marker(tmp_path):
    """R2 #10: the tarball is a PORTABLE package — a leftover
    ``portable.txt`` overrides installed.json (paths.py: portable beats
    installed), so the chosen ``--data-dir`` is silently ignored and
    data lands inside the install dir. The installer must strip it."""
    rel = tmp_path / "rel"
    rel.mkdir()
    tb = _fake_release(rel, portable=True)
    _write_sums(rel, tb)
    app, data = tmp_path / "app", tmp_path / "chosen data"
    r = _run_install_sh(
        tmp_path, "--from-dir", str(rel), "--version", "1.2.3",
        "--dir", str(app), "--data-dir", str(data))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (app / "portable.txt").exists()
    assert json.loads((app / "installed.json").read_text())[
        "data_dir"] == str(data)


# ---- R2 #12: `uv pip download` does not exist -------------------------------------


def test_package_unix_never_uses_uv_pip_download():
    """Every macOS/Linux tarball build ran ``uv pip download`` — a
    subcommand that does not exist — so all of them failed. The script
    mirrors package.ps1: standalone 3.11 + ``uv pip install --python``
    with the hashed lock."""
    bad = []
    for p in (ROOT / "scripts" / "package-unix.sh",
              ROOT / "scripts" / "package-linux.sh",
              ROOT / "scripts" / "package-dmg.sh",
              ROOT / "install.sh"):
        if "uv pip download" in p.read_text(encoding="utf-8",
                                            errors="replace"):
            bad.append(p.name)
    assert not bad, bad
    text = (ROOT / "scripts" / "package-unix.sh").read_text(
        encoding="utf-8")
    assert "uv python install" in text
    assert "uv pip install" in text
    assert "--require-hashes" in text


# ---- R2 #3: the cuda tarball can never publish (2 GiB cap) ------------------------


def test_install_sh_cuda_variant_provisions_cu124_online():
    """``--variant cuda`` must install the CPU payload then upgrade the
    bundled env to cu124 from the hashed lock — the linux-x64-cuda
    asset does not exist on the release page."""
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "linux-x64-cuda" not in text
    assert "requirements-cu124.txt" in text
    assert "download.pytorch.org/whl/cu124" in text
    assert "--require-hashes" in text


def test_package_unix_ships_uv_and_cuda_lock():
    """install.sh --variant cuda upgrades the bundled env online — the
    tarball must carry the uv binary and the hashed cu124 lock."""
    text = (ROOT / "scripts" / "package-unix.sh").read_text(
        encoding="utf-8")
    assert "requirements-cu124.txt" in text
    assert "tools/uv" in text


def test_package_unix_sh_lint():
    sh = _bash_sh()
    if sh is None:
        pytest.skip("no sh/bash on PATH")
    rc = subprocess.run(
        [sh, "-n", str(ROOT / "scripts" / "package-unix.sh")],
        capture_output=True, text=True, timeout=60,
    )
    assert rc.returncode == 0, rc.stderr


def test_package_unix_dry_run():
    if sys.platform != "win32":
        pytest.skip("dry-run guard only matters on Windows")
    sh = _bash_sh()
    if sh is None:
        pytest.skip("no sh/bash on PATH")
    out = subprocess.run(
        [sh, str(ROOT / "scripts" / "package-unix.sh"), "--dry-run"],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
        errors="replace",
    )
    assert "[dry-run]" in out.stdout


# ---- docker-compose contract ------------------------------------------------------

def test_manifests_and_docker_contract():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:8790:8790" in compose
    assert "/data" in compose and "SOUND_SYSTEM_DATA_DIR: /data" in compose
    for df in ("Dockerfile", "Dockerfile.cuda"):
        text = (ROOT / df).read_text(encoding="utf-8")
        assert "SOUND_SYSTEM_BIND_HOST=0.0.0.0" in text
        assert "SOUND_SYSTEM_IN_CONTAINER=1" in text
        assert "ffmpeg" in text
        assert "--extra" in text  # uv sync --frozen --extra cpu|cu124
        assert "EXPOSE 8790" in text


def test_docker_healthcheck_uses_venv_python():
    """The nvidia/cuda base has no `python` on PATH — the healthcheck must
    call the project venv interpreter directly."""
    for df in ("Dockerfile", "Dockerfile.cuda"):
        text = (ROOT / df).read_text(encoding="utf-8")
        hc = text[text.index("HEALTHCHECK"):]
        assert "/app/.venv/bin/python" in hc, df
        assert 'CMD ["python"' not in hc, df


def test_docker_uv_image_pinned_exact():
    """The uv installer image must be an exact version tag matching the
    dev env (0.12.x) — never a floating 0.5/0.x line."""
    import re
    for df in ("Dockerfile", "Dockerfile.cuda"):
        text = (ROOT / df).read_text(encoding="utf-8")
        m = re.search(r"ghcr\.io/astral-sh/uv:([0-9]+\.[0-9]+\.[0-9]+)", text)
        assert m, f"{df}: uv image not pinned to an exact version"
        assert m.group(1).split(".")[0] == "0" and \
            int(m.group(1).split(".")[1]) >= 12, (
                f"{df}: uv {m.group(1)} — expected the dev-env 0.12.x line")
        assert "uv:0.5" not in text, f"{df}: floating 0.5 tag"


def test_docker_cuda_single_python_source():
    """One python source only: uv-managed 3.11 installed outside /root so
    uid 10001 can read it — no apt python3.11 competing for resolution."""
    text = (ROOT / "Dockerfile.cuda").read_text(encoding="utf-8")
    assert "python3.11" not in text, "apt python3.11 must go — uv owns 3.11"
    assert "UV_PYTHON_INSTALL_DIR" in text
    assert "uv python install" in text
    cpu = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "uv python install" not in cpu  # base image already IS python:3.11


def test_manager_manifests_reference_assets():
    scoop = json.loads(
        (ROOT / "packaging/scoop/safa.json").read_text(encoding="utf-8"))
    url = scoop["architecture"]["64bit"]["url"]
    assert "Safa-" in url and "win-x64-cpu-portable.zip" in url

    winget = (ROOT / "packaging/winget/Safa.Safa.installer.yaml"
              ).read_text(encoding="utf-8")
    assert "win-x64-setup.exe" in winget

    brew = (ROOT / "packaging/homebrew/safa.rb").read_text(encoding="utf-8")
    assert "macos-arm64.tar.gz" in brew

    aur = (ROOT / "packaging/aur/PKGBUILD").read_text(encoding="utf-8")
    assert "linux-x64-cpu.tar.gz" in aur

@pytest.mark.skipif(PS is None, reason="powershell not on PATH")
def test_install_ps1_failure_under_iex_keeps_session_alive(tmp_path):
    """`irm … | iex` has no script file: a failure must throw, never
    `exit` — exit would close the user's whole PowerShell window."""
    wrapper = tmp_path / "iex_wrapper.ps1"
    ps1 = str(ROOT / "install.ps1")
    wrapper.write_text(
        "$sb = [scriptblock]::Create("
        "(Get-Content -LiteralPath '" + ps1 + "' -Raw -Encoding UTF8))\n"
        "try { & $sb -Repo 'https://github.com/<owner>/safa' }\n"
        "catch { 'CAUGHT: ' + $_.Exception.Message }\n"
        "'STILL-ALIVE'\n",
        encoding="utf-8-sig",
    )
    cp = subprocess.run(
        [PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
        capture_output=True, timeout=120,
    )
    out = cp.stdout.decode("utf-8", "replace")
    # an explicit <owner> -Repo keeps the placeholder guard live so
    # "latest" never reaches the network -> Stop-Safa path
    assert "CAUGHT: Safa install failed" in out, out
    assert "STILL-ALIVE" in out, out


@pytest.mark.skipif(PS is None, reason="powershell not on PATH")
def test_install_ps1_failure_as_file_exits_nonzero():
    cp = subprocess.run(
        [PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "install.ps1"),
         "-Repo", "https://github.com/<owner>/safa"],
        capture_output=True, timeout=120,
    )
    assert cp.returncode == 1
