"""Generate test fixtures into tests/fixtures/generated/ (idempotent).

Everything is rebuilt from scratch each run; safe to run repeatedly.
Requires ffmpeg (tools/ffmpeg/bin or PATH) and Windows SAPI for TTS.
Run as:  uv run python scripts/make_fixtures.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.ffmpeg import find_ffmpeg  # noqa: E402
from backend.core.paths import FIXTURES_DIR  # noqa: E402

SR = 48000
TEXT = "The quick brown fox jumps over the lazy dog. Sound system test number one."
ARABIC_NAME = "تسجيل اختبار 1.wav"
SEED = 20261114

# Text/path are passed via env vars: powershell -Command re-parses trailing
# argv and would split on spaces; env vars are Unicode- and space-safe.
SAPI_SCRIPT = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.SetOutputToWaveFile($env:SAPI_OUT); "
    "$s.Speak($env:SAPI_TEXT); "
    "$s.Dispose()"
)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x), dtype=np.float64)))


def _peak_norm(x: np.ndarray, peak: float = 0.95) -> np.ndarray:
    m = float(np.max(np.abs(x))) if x.size else 0.0
    return x * (peak / m) if m > peak else x


def _sapi_say(text: str, out_wav: Path) -> None:
    env = dict(os.environ, SAPI_OUT=str(out_wav), SAPI_TEXT=text)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", SAPI_SCRIPT],
        check=True,
        capture_output=True,
        env=env,
    )


def _load_mono_48k(path: Path) -> np.ndarray:
    data, sr = sf.read(path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SR:
        data = soxr.resample(data, sr, SR)
    return np.asarray(data, dtype=np.float32)


def _trim_silence(x: np.ndarray, frame_ms: int = 20, pad_ms: int = 60) -> np.ndarray:
    """Trim leading/trailing low-energy frames so recorded intervals match the
    real speech bounds (SAPI output has ~0.7 s quiet tails a VAD also cuts)."""
    frame = max(1, int(SR * frame_ms / 1000))
    n = len(x) // frame
    if n == 0:
        return x
    e = np.sqrt((x[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    thresh = max(float(e.max()) * 0.05, 1e-4)
    idx = np.flatnonzero(e > thresh)
    if idx.size == 0:
        return x
    pad = int(SR * pad_ms / 1000)
    a = max(0, int(idx[0]) * frame - pad)
    b = min(len(x), (int(idx[-1]) + 1) * frame + pad)
    return x[a:b]


def _synth_music(length: int, rng: np.random.Generator) -> np.ndarray:
    """Stereo music bed: chord of sines + drum pulses. Returns (n,2) float32."""
    t = np.arange(length) / SR
    chord_l = np.zeros(length, dtype=np.float32)
    chord_r = np.zeros(length, dtype=np.float32)
    for i, f in enumerate([110.0, 220.0, 277.18, 329.63]):
        detune = 1.0 + 0.001 * (i + 1)
        chord_l += np.sin(2 * np.pi * f * t + i).astype(np.float32) / 4
        chord_r += np.sin(2 * np.pi * f * detune * t + i + 0.4).astype(np.float32) / 4
    drums = np.zeros(length, dtype=np.float32)
    step = int(0.5 * SR)
    for k in range(0, length - step, step):
        n = min(int(0.06 * SR), length - k)
        env = np.exp(-np.linspace(0, 8, n)).astype(np.float32)
        drums[k : k + n] += rng.standard_normal(n).astype(np.float32) * env * 0.5
    return np.stack([chord_l + drums, chord_r + drums * 0.9], axis=1)


def main() -> None:
    rng = np.random.default_rng(SEED)
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FIXTURES_DIR / "_tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        # --- speech.wav: mono SAPI speech at 48 kHz
        raw = tmp / "sapi_raw.wav"
        _sapi_say(TEXT, raw)
        speech = _trim_silence(_load_mono_48k(raw))
        sf.write(FIXTURES_DIR / "speech.wav", _peak_norm(speech), SR)
        speech = _peak_norm(speech)
        d = len(speech) / SR
        sp_rms = _rms(speech)

        # --- speech_noise.wav: 0.5 s noise-only head, then speech+noise @ 5 dB SNR (mono)
        head = int(0.5 * SR)
        noise = rng.standard_normal(head + len(speech)).astype(np.float32)
        noise *= (sp_rms / (10 ** (5 / 20))) / _rms(noise)
        mix = np.concatenate([np.zeros(head, dtype=np.float32), speech]) + noise
        sf.write(FIXTURES_DIR / "speech_noise.wav", _peak_norm(mix), SR)

        # --- speech_music.wav: stereo speech + music bed at -6 dB relative
        music = _synth_music(len(speech), rng)
        music *= (sp_rms * (10 ** (-6 / 20))) / _rms(music)
        stereo = np.stack([speech, speech], axis=1) + music
        sf.write(FIXTURES_DIR / "speech_music.wav", _peak_norm(stereo), SR)

        # --- speech_silences.wav: speech | 3s | speech | 5s | speech (mono)
        g1, g2 = np.zeros(int(3.0 * SR), np.float32), np.zeros(int(5.0 * SR), np.float32)
        sig = np.concatenate([speech, g1, speech, g2, speech])
        sf.write(FIXTURES_DIR / "speech_silences.wav", sig, SR)
        sil = [[d, d + 3.0], [2 * d + 3.0, 2 * d + 8.0]]
        spk = [[0.0, d], [d + 3.0, 2 * d + 3.0], [2 * d + 8.0, 3 * d + 8.0]]

        # --- silence.wav: 5 s digital silence (mono)
        sf.write(FIXTURES_DIR / "silence.wav", np.zeros(int(5.0 * SR), np.float32), SR)

        # --- clip.mp4: 10 s testsrc2 640x360@25 h264 + AAC from speech_music.wav (looped)
        subprocess.run(
            [
                str(find_ffmpeg()), "-y",
                "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
                "-stream_loop", "-1", "-i", str(FIXTURES_DIR / "speech_music.wav"),
                "-t", "10",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                str(FIXTURES_DIR / "clip.mp4"),
            ],
            check=True,
            capture_output=True,
        )

        # --- Arabic + spaces filename: copy of speech_noise.wav
        shutil.copyfile(FIXTURES_DIR / "speech_noise.wav", FIXTURES_DIR / ARABIC_NAME)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    dur = lambda n: round(n / SR, 3)  # noqa: E731
    noise_dur = dur(int(0.5 * SR) + len(speech))
    manifest = {
        "files": [
            {"file": "speech.wav", "duration_s": dur(len(speech)), "sample_rate": SR,
             "channels": 1, "text": TEXT, "speech_intervals": [[0.0, round(d, 3)]]},
            {"file": "speech_noise.wav", "duration_s": noise_dur, "sample_rate": SR,
             "channels": 1, "speech_intervals": [[0.5, round(0.5 + d, 3)]]},
            {"file": "speech_music.wav", "duration_s": dur(len(speech)), "sample_rate": SR,
             "channels": 2, "speech_intervals": [[0.0, round(d, 3)]]},
            {"file": "speech_silences.wav", "duration_s": dur(len(sig)), "sample_rate": SR,
             "channels": 1, "speech_intervals": [[round(a, 3) for a in p] for p in spk],
             "silence_intervals": [[round(a, 3) for a in p] for p in sil]},
            {"file": "silence.wav", "duration_s": 5.0, "sample_rate": SR, "channels": 1,
             "speech_intervals": [], "silence_intervals": [[0.0, 5.0]]},
            {"file": "clip.mp4", "duration_s": 10.0, "sample_rate": SR, "channels": 2},
            {"file": ARABIC_NAME, "duration_s": noise_dur, "sample_rate": SR, "channels": 1,
             "speech_intervals": [[0.5, round(0.5 + d, 3)]]},
        ]
    }
    (FIXTURES_DIR / "fixtures.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {len(manifest['files'])} fixtures to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
