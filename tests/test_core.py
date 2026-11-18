"""Task 1+2 tests: ffmpeg resolution, CUDA DLL setup, model manifest."""
import os
from pathlib import Path

from backend.core.paths import TOOLS_DIR, MODELS_DIR
from backend.core.ffmpeg import find_ffmpeg, find_ffprobe, ffmpeg_version, has_encoder
from backend.core.gpu import setup_cuda_dlls
from backend.core.models_manifest import MODELS, model_dir, configure_model_env


def test_find_ffmpeg_prefers_tools_dir():
    p = find_ffmpeg()
    assert p.exists() and p.parent == TOOLS_DIR / "ffmpeg" / "bin"


def test_find_ffprobe_exists():
    assert find_ffprobe().exists()


def test_ffmpeg_version_string():
    assert ffmpeg_version().startswith("ffmpeg version")


def test_has_libx264():
    assert has_encoder("libx264")


def test_setup_cuda_dlls_returns_list():
    assert isinstance(setup_cuda_dlls(), list)


def test_cuda_dll_dirs_against_fixture_tree(tmp_path, monkeypatch):
    """R1 weak test: ``cuda_dll_dirs`` must answer from the on-disk
    ``<site-packages>/nvidia/*/bin`` layout, not just return a list."""
    from backend.core import gpu

    sp = tmp_path / "site-packages"
    (sp / "nvidia" / "cudnn" / "bin").mkdir(parents=True)
    (sp / "nvidia" / "cublas" / "bin").mkdir(parents=True)
    (sp / "nvidia" / "no_bin_pkg").mkdir()  # package without bin/ ignored
    monkeypatch.setattr(gpu, "_site_packages_dirs", lambda: [sp])
    assert gpu.cuda_dll_dirs() == [
        sp / "nvidia" / "cublas" / "bin",
        sp / "nvidia" / "cudnn" / "bin",
    ]
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(gpu, "_site_packages_dirs", lambda: [empty])
    assert gpu.cuda_dll_dirs() == []


def test_model_dirs_under_models():
    for name in MODELS:
        assert MODELS_DIR in model_dir(name).parents


def test_configure_model_env_sets_hf_home():
    configure_model_env()
    assert Path(os.environ["HF_HOME"]).is_relative_to(MODELS_DIR)


def test_configure_model_env_overrides_existing(monkeypatch):
    """M1 review fix: a global user env var must not redirect model caches."""
    monkeypatch.setenv("HF_HOME", r"C:\elsewhere\hf")
    monkeypatch.setenv("TORCH_HOME", r"C:\elsewhere\torch")
    configure_model_env()
    assert Path(os.environ["HF_HOME"]).is_relative_to(MODELS_DIR)
    assert Path(os.environ["HF_HUB_CACHE"]).is_relative_to(MODELS_DIR)
    assert Path(os.environ["TORCH_HOME"]).is_relative_to(MODELS_DIR)
    assert Path(os.environ["XDG_CACHE_HOME"]).is_relative_to(MODELS_DIR)


# ---- v0.1.1: Windows CUDA DLLs come from torch/lib ------------------------------


def _fake_torch_lib(sp: Path, *, cuda: bool) -> Path:
    tl = sp / "torch" / "lib"
    tl.mkdir(parents=True)
    (tl / "torch_cpu.dll").write_bytes(b"x")
    if cuda:
        for n in ("torch_cuda.dll", "cudnn64_9.dll", "cublas64_12.dll"):
            (tl / n).write_bytes(b"x")
    return tl


def test_cuda_dll_dirs_includes_cuda_torch_lib_on_windows(tmp_path, monkeypatch):
    from backend.core import gpu

    sp = tmp_path / "site-packages"
    tl = _fake_torch_lib(sp, cuda=True)
    monkeypatch.setattr(gpu, "_site_packages_dirs", lambda: [sp])
    assert gpu.cuda_dll_dirs(is_nt=True) == [tl]
    # POSIX resolves .so's from nvidia/*/lib wheels, never torch/lib
    assert gpu.cuda_dll_dirs(is_nt=False) == []


def test_cpu_torch_lib_does_not_count_as_cuda(tmp_path, monkeypatch):
    """CPU torch has torch/lib too — counting it would claim a usable GPU."""
    from backend.core import gpu

    sp = tmp_path / "site-packages"
    _fake_torch_lib(sp, cuda=False)
    monkeypatch.setattr(gpu, "_site_packages_dirs", lambda: [sp])
    assert gpu.cuda_dll_dirs(is_nt=True) == []


def test_nvidia_wheels_and_torch_lib_both_listed(tmp_path, monkeypatch):
    """Pre-0.1.1 Windows installs still have nvidia/*/bin: keep them first."""
    from backend.core import gpu

    sp = tmp_path / "site-packages"
    (sp / "nvidia" / "cudnn" / "bin").mkdir(parents=True)
    tl = _fake_torch_lib(sp, cuda=True)
    monkeypatch.setattr(gpu, "_site_packages_dirs", lambda: [sp])
    assert gpu.cuda_dll_dirs(is_nt=True) == [sp / "nvidia" / "cudnn" / "bin", tl]
