"""M10c §2: one uv.lock covers both variants via conflicting extras.

`cpu`     → +cpu torch trio from the pytorch cpu index (PyPI on
            darwin/aarch64 where no +cpu wheels exist).
`cu124`   → +cu124 torch trio + onnxruntime-gpu (+ nvidia-* wheels on
            Linux only — Windows torch/lib already ships those DLLs)
            (PyPI torch + onnxruntime on darwin).
no extra  → working CPU-capable baseline (PyPI torch).

Exactly one ONNX Runtime distribution must resolve per platform:
onnxruntime-gpu ships no darwin/aarch64 wheels, so the CPU-only
onnxruntime wheel is kept alive there via a tool.uv override.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"

TORCH_PKGS = ("torch", "torchaudio", "torchvision")


def _toml() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _export(*extras: str) -> str:
    """`uv export` requirements text for the given extras (frozen lock)."""
    cmd = [
        "uv", "export", "--frozen", "--no-dev", "--no-hashes",
        "--directory", str(ROOT),
    ]
    for e in extras:
        cmd += ["--extra", e]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, f"uv export failed: {p.stderr[-800:]}"
    return p.stdout


def _lines(export: str, name: str) -> list[str]:
    return [l for l in export.splitlines()
            if l.split(";")[0].split("=")[0].strip().rstrip("=") == name
            or l.startswith(name + "==")]


# ---- static pyproject contract -------------------------------------------------


def test_conflicting_extras_declared():
    cfg = _toml()["tool"]["uv"]
    conflicts = cfg.get("conflicts") or []
    pair = None
    for group in conflicts:
        names = {e["extra"] for e in group if "extra" in e}
        if {"cpu", "cu124"} <= names:
            pair = group
    assert pair is not None, "tool.uv.conflicts must declare cpu vs cu124"


def test_torch_sources_keyed_by_extra():
    cfg = _toml()["tool"]["uv"]
    for pkg in TORCH_PKGS:
        entries = cfg["sources"].get(pkg)
        assert entries, f"{pkg}: no tool.uv.sources entries"
        extras = {e.get("extra") for e in entries}
        assert {"cpu", "cu124"} <= extras, (
            f"{pkg}: sources must map cpu and cu124 extras, got {extras}")
        indexes = {e["index"] for e in entries}
        assert {"pytorch-cpu", "pytorch-cu124"} <= indexes


def test_indexes_pinned():
    cfg = _toml()["tool"]["uv"]
    urls = {i["name"]: i["url"] for i in cfg.get("index", [])}
    assert urls["pytorch-cu124"].endswith("/cu124")
    assert urls["pytorch-cpu"].endswith("/cpu")


def test_lock_is_fresh():
    """uv.lock must match pyproject.toml (CI guard)."""
    p = subprocess.run(
        ["uv", "lock", "--check", "--directory", str(ROOT)],
        capture_output=True, text=True, timeout=120,
    )
    assert p.returncode == 0, (
        f"uv.lock is stale — run `uv lock`. {p.stdout}{p.stderr}")


# ---- per-fork wheel selection ---------------------------------------------------


def test_cpu_extra_selects_cpu_torch():
    out = _export("cpu")
    torch_lines = _lines(out, "torch")
    assert any("torch==2.5.1+cpu" in l for l in torch_lines), (
        "cpu extra must produce +cpu builds somewhere")
    assert all("+cu124" not in l for l in torch_lines)
    assert not any(l.startswith("nvidia-") for l in out.splitlines()), (
        "cpu extra must not pull nvidia wheels on any platform")


def test_cu124_extra_selects_cuda_stack():
    out = _export("cu124")
    for pkg in TORCH_PKGS:
        assert any(l.startswith(pkg) and "+cu124" in l
                   for l in _lines(out, pkg)), f"{pkg}: missing +cu124 line"
    assert any(l.startswith("onnxruntime-gpu") for l in out.splitlines())
    assert any(l.startswith("nvidia-cublas-cu12") for l in out.splitlines())


def test_cu124_nvidia_wheels_are_linux_only():
    """v0.1.1: on Windows the +cu124 torch wheel already bundles cuBLAS,
    cuDNN 9, cuFFT... in torch/lib; the nvidia-* wheels were a ~2.7 GB
    duplicate. Every nvidia line must be gated to Linux."""
    out = _export("cu124")
    nv = [l for l in out.splitlines() if l.startswith("nvidia-")]
    assert nv
    for l in nv:
        assert "sys_platform == 'linux'" in l, f"not linux-gated: {l!r}"


def test_exactly_one_ort_dist_per_platform():
    """ort dists' markers must be mutually exclusive on every platform."""
    for extras in (("cpu",), ("cu124",), ()):
        out = _export(*extras)
        ort = [l for l in out.splitlines() if l.startswith("onnxruntime==")]
        ort_gpu = [l for l in out.splitlines()
                   if l.startswith("onnxruntime-gpu==")]
        for l in ort:
            assert "darwin" in l or "aarch64" in l, (
                f"{extras}: plain onnxruntime must be darwin/aarch64-only, "
                f"got {l!r}")
        for l in ort_gpu:
            assert "aarch64" in l and "darwin" in l, (
                f"{extras}: onnxruntime-gpu must be win/linux-x64-only, "
                f"got {l!r}")


def test_no_extra_is_a_working_baseline():
    out = _export()
    assert any(l.startswith("torch==2.5.1") for l in out.splitlines())
    assert any(l.startswith("fastapi") for l in out.splitlines())
    assert any(l.startswith("faster-whisper") for l in out.splitlines())


def test_darwin_cu124_degrades_to_pypi():
    """darwin+cu124 → PyPI torch + onnxruntime (no CUDA wheels on macOS)."""
    out = _export("cu124")
    assert any(l.startswith("torch==2.5.1 ;") and "darwin" in l
               for l in out.splitlines())
    ort = [l for l in out.splitlines() if l.startswith("onnxruntime==")]
    assert any("darwin" in l for l in ort)
