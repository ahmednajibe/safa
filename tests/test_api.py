"""Task 2 tests: capability detection + /api/health."""
import subprocess
import sys

from fastapi.testclient import TestClient

from backend.core.paths import ROOT
from backend.core.settings import Settings
from backend.main import create_app


def _client(tmp_path):
    return TestClient(
        create_app(Settings(data_dir=tmp_path, db_path=tmp_path / "app.db"))
    )


def test_health_ok(tmp_path):
    r = _client(tmp_path).get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ffmpeg"]["path"].lower().endswith("ffmpeg.exe")
    assert body["ffmpeg"]["ffprobe_path"].lower().endswith("ffprobe.exe")
    assert body["device"] in ("gpu", "cpu")
    assert set(body["modules"]) >= {
        "torch", "df", "audio_separator", "silero_vad",
        "pedalboard", "faster_whisper", "onnxruntime",
    }
    assert set(body["models"]) >= {
        "htdemucs", "kim_vocal_2", "deepfilternet3",
        "whisper_small", "silero_vad",
    }


def test_health_does_not_import_torch():
    """API process must stay light: no ML imports (startup < 3 s)."""
    code = (
        "import sys\n"
        "from fastapi.testclient import TestClient\n"
        "from backend.main import app\n"
        "r = TestClient(app).get('/api/health')\n"
        "assert r.status_code == 200, r.text\n"
        "bad = [m for m in sys.modules if m == 'torch' or m.startswith('torch.')]\n"
        "assert not bad, f'torch imported in API process: {bad[:3]}'\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert out.returncode == 0, out.stderr


def test_detect_capabilities_cached_and_refresh():
    from backend.core.capabilities import detect_capabilities, refresh_capabilities

    a = detect_capabilities()
    assert a is detect_capabilities()  # cached
    b = refresh_capabilities()
    assert b is detect_capabilities()
    assert b is not a


def test_detect_models_present():
    """All M1-downloaded models detected on this machine."""
    from backend.core.capabilities import detect_capabilities

    caps = detect_capabilities()
    missing = [k for k, v in caps.models.items() if not v]
    assert not missing, f"models not detected: {missing}"
