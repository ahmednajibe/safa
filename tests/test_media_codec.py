"""M3a Task 2 tests: decode, encode, remux, peaks, loudness."""
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from backend.core.ffmpeg import find_ffprobe
from backend.core.proc import run
from backend.media.decode import decode_to_wav
from backend.media.encode import (
    AUDIO_FORMATS,
    encode_audio,
    make_playback_copy,
    remux_video,
    video_container_for,
)
from backend.media.loudness import integrated_lufs
from backend.media.peaks import compute_peaks
from backend.media.probe import probe

GENERATED = Path(__file__).parent / "fixtures" / "generated"
CLIP = GENERATED / "clip.mp4"
SPEECH = GENERATED / "speech.wav"
SILENCE = GENERATED / "silence.wav"


def _probe_streams(path: Path) -> list[dict]:
    cp = run([
        find_ffprobe(), "-v", "error", "-print_format", "json",
        "-show_streams", path,
    ])
    return json.loads(cp.stdout)["streams"]


def test_decode_float32_48k(tmp_path):
    dst = tmp_path / "out.wav"
    info = decode_to_wav(CLIP, dst, stream_index=1)
    assert info.sample_rate == 48000
    assert info.channels == 2
    assert abs(info.duration_s - 10.0) < 0.02
    si = sf.info(dst)
    assert si.subtype == "FLOAT"
    assert si.samplerate == 48000


def test_decode_real_m4a_duration_matches_probe(real_dir, tmp_path):
    src = real_dir / "Recording (2).m4a"
    p = probe(src)
    info = decode_to_wav(src, tmp_path / "out.wav", stream_index=0)
    assert info.channels == 2
    assert abs(info.duration_s - p.duration_s) < 0.025


def test_decode_range(tmp_path):
    info = decode_to_wav(
        CLIP, tmp_path / "seg.wav", stream_index=1, start_s=2.0, end_s=5.0,
    )
    assert abs(info.duration_s - 3.0) < 0.02


def test_decode_range_real_m4a(real_dir, tmp_path):
    info = decode_to_wav(
        real_dir / "Recording (2).m4a", tmp_path / "seg.wav",
        stream_index=0, start_s=2.0, end_s=5.0,
    )
    assert abs(info.duration_s - 3.0) < 0.02


def test_decode_downmix_6ch(tmp_path):
    src = tmp_path / "six.wav"
    sf.write(src, np.zeros((48000, 6), dtype=np.float32), 48000, subtype="FLOAT")
    info = decode_to_wav(src, tmp_path / "out.wav", stream_index=0)
    assert info.channels == 2


def test_decode_stereo_stays_stereo(tmp_path):
    info = decode_to_wav(CLIP, tmp_path / "out.wav", stream_index=1)
    assert info.channels == 2


def test_encode_each_format(tmp_path):
    src = tmp_path / "in.wav"
    sf.write(
        src,
        0.2 * np.ones((48000, 2), dtype=np.float32),
        48000, subtype="FLOAT",
    )
    expected = {"wav": "pcm_s24le", "flac": "flac", "mp3": "mp3", "aac": "aac"}
    assert set(AUDIO_FORMATS) == {"wav", "flac", "mp3", "aac"}
    for fmt, codec in expected.items():
        out = encode_audio(src, tmp_path / f"out_{fmt}", fmt)
        assert out.exists()
        streams = _probe_streams(out)
        assert streams[0]["codec_name"] == codec, (fmt, streams[0]["codec_name"])


def test_remux_copies_video(tmp_path):
    wav = tmp_path / "audio.wav"
    decode_to_wav(CLIP, wav, stream_index=1)
    src_packets = _count_video_packets(CLIP)
    out = remux_video(CLIP, 0, wav, tmp_path / "result", "h264")
    assert out.suffix == ".mp4"
    streams = _probe_streams(out)
    v = next(s for s in streams if s["codec_type"] == "video")
    a = next(s for s in streams if s["codec_type"] == "audio")
    assert v["codec_name"] == "h264"
    assert (v["width"], v["height"]) == (640, 360)
    assert a["codec_name"] == "aac"
    p = probe(out)
    assert abs(p.duration_s - 10.0) < 0.05
    assert _count_video_packets(out) == src_packets


def _count_video_packets(path: Path) -> int:
    cp = run([
        find_ffprobe(), "-v", "error", "-select_streams", "v:0",
        "-count_packets", "-show_entries", "stream=nb_read_packets",
        "-of", "csv=p=0", path,
    ])
    return int(cp.stdout.strip())


def test_remux_mkv_for_other_codecs(tmp_path):
    assert video_container_for("h264") == "mp4"
    assert video_container_for("hevc") == "mp4"
    assert video_container_for("av1") == "mp4"
    assert video_container_for("vp9") == "mkv"


def test_make_playback_copy(tmp_path):
    out = make_playback_copy(SPEECH, tmp_path / "play.m4a")
    streams = _probe_streams(out)
    assert streams[0]["codec_name"] == "aac"
    assert out.exists()


def test_peaks_shape(tmp_path):
    wav = tmp_path / "x.wav"
    sf.write(
        wav,
        np.zeros((48000 * 2, 2), dtype=np.float32),
        48000, subtype="FLOAT",
    )
    result = compute_peaks(wav, per_second=50)
    assert result["sample_rate"] == 48000
    assert result["channels"] == 2
    assert abs(result["duration_s"] - 2.0) < 0.01
    assert result["per_second"] == 50
    assert len(result["data"]) == 2
    for ch in result["data"]:
        assert abs(len(ch) - 2.0 * 50) <= 2
        for mn, mx in ch:
            assert -1.0 <= mn <= mx <= 1.0


def test_peaks_values():
    wav_amp = GENERATED / "speech_music.wav"
    result = compute_peaks(wav_amp, per_second=20)
    mx = max(pair[1] for ch in result["data"] for pair in ch)
    mn = min(pair[0] for ch in result["data"] for pair in ch)
    assert mx > 0.1 and mn < -0.1


def test_peaks_respects_max_points(tmp_path):
    wav = tmp_path / "long.wav"
    sf.write(
        wav,
        np.zeros((48000 * 30, 1), dtype=np.float32),
        48000, subtype="FLOAT",
    )
    result = compute_peaks(wav, per_second=50, max_points=300)
    n = len(result["data"][0])
    assert n <= 300 + 1  # buckets, not per-second*duration
    assert result["per_second"] < 50


def test_lufs_speech():
    lufs = integrated_lufs(SPEECH)
    assert lufs is not None
    assert -40.0 <= lufs <= -5.0


def test_lufs_silence_none():
    assert integrated_lufs(SILENCE) is None
