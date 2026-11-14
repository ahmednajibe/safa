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
