"""Task 3 tests: generated fixtures exist and are readable (incl. Arabic name)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import make_fixtures  # noqa: E402

from backend.core.ffmpeg import find_ffprobe  # noqa: E402
from backend.core.paths import FIXTURES_DIR  # noqa: E402

ARABIC_NAME = "تسجيل اختبار 1.wav"


@pytest.fixture(scope="session", autouse=True)
def fixtures_generated():
    if not (FIXTURES_DIR / "fixtures.json").exists():
        make_fixtures.main()
    return FIXTURES_DIR / "fixtures.json"


def _meta() -> dict:
    return json.loads((FIXTURES_DIR / "fixtures.json").read_text(encoding="utf-8"))


def _fixture_meta(filename: str) -> dict:
    for entry in _meta()["files"]:
        if entry["file"] == filename:
            return entry
    raise AssertionError(f"{filename} not in fixtures.json")


def _ffprobe_json(path: Path) -> dict:
    out = subprocess.run(
        [
            str(find_ffprobe()),
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_all_fixture_files_exist():
    for entry in _meta()["files"]:
        assert (FIXTURES_DIR / entry["file"]).exists(), entry["file"]


def test_arabic_name_readable():
    info = sf.info(FIXTURES_DIR / ARABIC_NAME)
    assert info.samplerate == 48000


def test_arabic_name_ffprobe():
    data = _ffprobe_json(FIXTURES_DIR / ARABIC_NAME)
    assert float(data["format"]["duration"]) > 0


def test_silence_intervals_recorded():
    meta = _fixture_meta("speech_silences.wav")
    assert [round(b - a, 1) for a, b in meta["silence_intervals"]] == [3.0, 5.0]


def test_clip_mp4_has_video_and_audio():
    streams = _ffprobe_json(FIXTURES_DIR / "clip.mp4")["streams"]
    kinds = [s["codec_type"] for s in streams]
    assert "video" in kinds and "audio" in kinds
    video = next(s for s in streams if s["codec_type"] == "video")
    assert video["codec_name"] == "h264"


def test_make_fixtures_idempotent():
    make_fixtures.main()
    make_fixtures.main()
