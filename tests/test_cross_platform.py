"""M10c: POSIX-safe helpers — reveal in file manager, CUDA lib dirs on
Linux, executable bit on bundled ffmpeg binaries."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.core import ffmpeg, gpu, output_dir


# ---- reveal_in_explorer --------------------------------------------------------


def _reveal_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: calls.append(list(argv)))
    return calls


def test_reveal_windows_selects_file(monkeypatch, tmp_path):
    calls = _reveal_calls(monkeypatch)
    f = tmp_path / "out file.wav"
    output_dir.reveal_in_explorer(f, sys_platform="win32")
    assert calls == [["explorer.exe", f'/select,"{f}"']]


def test_reveal_macos_uses_open_dash_r(monkeypatch, tmp_path):
    calls = _reveal_calls(monkeypatch)
    f = tmp_path / "out file.wav"
    output_dir.reveal_in_explorer(f, sys_platform="darwin")
    assert calls == [["open", "-R", str(f)]]


def test_reveal_linux_opens_parent_dir(monkeypatch, tmp_path):
    calls = _reveal_calls(monkeypatch)
    f = tmp_path / "out file.wav"
    output_dir.reveal_in_explorer(f, sys_platform="linux")
    assert calls == [["xdg-open", str(f.parent)]]


def test_reveal_linux_directory_opens_itself(monkeypatch, tmp_path):
    calls = _reveal_calls(monkeypatch)
    d = tmp_path / "results"
    d.mkdir()
    output_dir.reveal_in_explorer(d, sys_platform="linux")
    assert calls == [["xdg-open", str(d)]]


# ---- cuda_dll_dirs on the POSIX wheel layout -----------------------------------


def _fake_site_packages(tmp_path: Path, sub: str) -> Path:
    sp = tmp_path / "site-packages"
    (sp / "nvidia" / "cublas" / sub).mkdir(parents=True)
    (sp / "nvidia" / "cudnn" / sub).mkdir(parents=True)
    (sp / "nvidia" / "not-a-lib").mkdir(parents=True)
    return sp


def test_cuda_dll_dirs_posix_uses_lib(tmp_path, monkeypatch):
    sp = _fake_site_packages(tmp_path, "lib")
    monkeypatch.setattr(gpu, "_site_packages_dirs", lambda: [sp])
    dirs = gpu.cuda_dll_dirs(is_nt=False)
    assert dirs == [sp / "nvidia" / "cublas" / "lib",
                    sp / "nvidia" / "cudnn" / "lib"]


def test_cuda_dll_dirs_nt_uses_bin(tmp_path, monkeypatch):
    sp = _fake_site_packages(tmp_path, "bin")
    monkeypatch.setattr(gpu, "_site_packages_dirs", lambda: [sp])
    dirs = gpu.cuda_dll_dirs(is_nt=True)
    assert dirs == [sp / "nvidia" / "cublas" / "bin",
                    sp / "nvidia" / "cudnn" / "bin"]


def test_cuda_dll_dirs_wrong_layout_empty(tmp_path, monkeypatch):
    """Linux nvidia wheels have no bin/ — a POSIX tree must not fake
    the Windows check and vice versa."""
    sp = _fake_site_packages(tmp_path, "lib")
    monkeypatch.setattr(gpu, "_site_packages_dirs", lambda: [sp])
    assert gpu.cuda_dll_dirs(is_nt=True) == []


def test_setup_cuda_dlls_posix_ld_library_path(tmp_path, monkeypatch):
    sp = _fake_site_packages(tmp_path, "lib")
    monkeypatch.setattr(gpu, "_site_packages_dirs", lambda: [sp])
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    added = gpu.setup_cuda_dlls(is_nt=False)
    assert added == [sp / "nvidia" / "cublas" / "lib",
                     sp / "nvidia" / "cudnn" / "lib"]
    assert os.environ["LD_LIBRARY_PATH"].split(os.pathsep)[:2] == [
        str(sp / "nvidia" / "cublas" / "lib"),
        str(sp / "nvidia" / "cudnn" / "lib"),
    ]


def test_setup_cuda_dlls_posix_never_sets_path(tmp_path, monkeypatch):
    """PATH has nothing to do with .so resolution on POSIX."""
    sp = _fake_site_packages(tmp_path, "lib")
    monkeypatch.setattr(gpu, "_site_packages_dirs", lambda: [sp])
    before = os.environ.get("PATH", "")
    gpu.setup_cuda_dlls(is_nt=False)
    assert os.environ["PATH"] == before


# ---- ffmpeg exec bit on POSIX ----------------------------------------------------


def test_ensure_executable_chmods_on_posix(tmp_path, monkeypatch):
    f = tmp_path / "ffmpeg"
    f.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(Path, "chmod", lambda self, m: calls.append(m))
    monkeypatch.setattr(os, "access", lambda p, m: False)
    ffmpeg._ensure_executable(f, is_nt=False)
    assert calls == [0o755]


def test_ensure_executable_skips_when_executable(tmp_path, monkeypatch):
    f = tmp_path / "ffmpeg"
    f.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(Path, "chmod", lambda self, m: calls.append(m))
    monkeypatch.setattr(os, "access", lambda p, m: True)
    ffmpeg._ensure_executable(f, is_nt=False)
    assert calls == []


def test_ensure_executable_noop_on_windows(tmp_path, monkeypatch):
    f = tmp_path / "ffmpeg.exe"
    f.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(Path, "chmod", lambda self, m: calls.append(m))
    ffmpeg._ensure_executable(f, is_nt=True)
    assert calls == []
