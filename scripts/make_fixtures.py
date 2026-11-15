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
from scipy.signal import butter, sosfiltfilt

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


def _saw(phase_t: np.ndarray, freq: float, harmonics: int = 8) -> np.ndarray:
    """Band-limited sawtooth via additive synthesis (freq in Hz)."""
    out = np.zeros_like(phase_t)
    for k in range(1, harmonics + 1):
        out += np.sin(2 * np.pi * freq * k * phase_t) / k
    return out * (2.0 / np.pi)


def _synth_sep_music(length: int) -> np.ndarray:
    """M7 separation fixture music bed (deterministic — no RNG).

    Harmonic sawtooth chords changing every 2 s (low-passed at 3.5 kHz),
    an eighth-note bassline on the chord roots, and drum transients
    (kick every beat, noise hats off-beat). Stereo with slight detune.
    Returns (n, 2) float32.
    """
    rng = np.random.default_rng(7)
    t = np.arange(length) / SR
    left = np.zeros(length, dtype=np.float64)
    right = np.zeros(length, dtype=np.float64)

    # Chord pad: A4 F4 C5 G4 progression, minor-ish voicing, new chord
    # every 2 s, low-passed at 3.5 kHz so it does not fight formants.
    # Voiced an octave up: roots at speech-fundamental pitch (170-260 Hz)
    # get partially classified as voice by the demucs model.
    roots = (440.00, 349.23, 523.25, 392.00)
    intervals = (1.0, 1.18921, 1.49831, 2.0)  # root, m3, P5, octave
    chord_n = int(2.0 * SR)
    n_chords = int(np.ceil(length / chord_n))
    for c in range(n_chords):
        start = c * chord_n
        stop = min(length, start + chord_n)
        tt = t[start:stop]
        root = roots[c % len(roots)]
        # short attack/release avoids clicks between chords
        env = np.ones(stop - start)
        ramp = min(int(0.02 * SR), len(env) // 2)
        env[:ramp] = np.linspace(0, 1, ramp)
        env[-ramp:] = np.linspace(1, 0, ramp)
        for j, iv in enumerate(intervals):
            left[start:stop] += 0.25 * _saw(tt, root * iv) * env
            right[start:stop] += (
                0.25 * _saw(tt + 0.0007, root * iv * (1 + 0.0015 * (j + 1))) * env
            )
    sos = butter(4, 3500, btype="low", fs=SR, output="sos")
    left = sosfiltfilt(sos, left)
    right = sosfiltfilt(sos, right)

    # Bassline: eighth-note cells, root an octave down alternating with the
    # fifth.
    cell = int(0.25 * SR)
    for c in range(n_chords):
        c_start = c * chord_n
        c_stop = min(length, c_start + chord_n)
        root = roots[c % len(roots)] / 2.0
        n_cells = (c_stop - c_start) // cell
        for k in range(n_cells):
            s = c_start + k * cell
            e = min(c_stop, s + cell)
            f = root if k % 2 == 0 else root * 1.49831
            tt = t[s:e]
            env = np.exp(-(tt - tt[0]) * 9.0)
            note = (
                np.sin(2 * np.pi * f * tt)
                + 0.5 * np.sin(4 * np.pi * f * tt)
                + 0.25 * np.sin(6 * np.pi * f * tt)
                + 0.15 * np.sin(8 * np.pi * f * tt)
            )
            left[s:e] += 0.2 * note * env
            right[s:e] += 0.2 * note * env

    # Drums: kick every 0.5 s (sine drop + click), noise hats on off-beats —
    # transient content the separator must keep in the music stem.
    for i in range(int(length / (0.5 * SR))):
        s = int(i * 0.5 * SR)
        n = min(int(0.18 * SR), length - s)
        tt = np.arange(n) / SR
        kick = np.sin(2 * np.pi * (120 - 70 * tt / 0.18) * tt) * np.exp(-tt * 30)
        n_click = min(int(0.005 * SR), n)
        click = (
            rng.standard_normal(n_click)
            * np.exp(-np.arange(n_click) / (0.001 * SR))
        )
        kick[:n_click] += 0.5 * click
        left[s:s + n] += 0.3 * kick
        right[s:s + n] += 0.3 * kick
        cs = s + int(0.25 * SR)
        hat_n = int(0.03 * SR)
        if cs + hat_n < length:
            hat = rng.standard_normal(hat_n) * np.exp(-np.linspace(0, 9, hat_n))
            left[cs:cs + hat_n] += 0.25 * hat
            right[cs:cs + hat_n] += 0.25 * np.roll(hat, 5)

    m = np.stack([left, right], axis=1)
    m /= np.max(np.abs(m)) + 1e-9
    return m.astype(np.float32)


def _write_sep_fixture(speech: np.ndarray) -> dict:
    """M7 fixture: speech mixed into a synthetic music bed at -10 dB SMR.

    Layout: 1 s music-only intro | speech | 1.5 s music-only gap | speech |
    0.5 s music tail. Ground-truth voice/music stems are written next to
    the mix so tests can score SI-SDR. Returns manifest speech intervals.
    """
    n_sp = len(speech)
    intro, gap, tail = int(1.0 * SR), int(1.5 * SR), int(0.5 * SR)
    n = intro + n_sp + gap + n_sp + tail

    music = _synth_sep_music(n)

    voice = np.zeros((n, 2), dtype=np.float32)
    seg1 = (intro, intro + n_sp)
    seg2 = (intro + n_sp + gap, intro + n_sp + gap + n_sp)
    for s, e in (seg1, seg2):
        voice[s:e, 0] = speech
        voice[s:e, 1] = speech

    # -10 dB SMR: the music bed sits 10 dB below the speech level (same
    # convention as speech_music.wav's "-6 dB relative" music gain).
    g = _rms(speech) * (10.0 ** (-10.0 / 20.0)) / (_rms(music) + 1e-9)
    music = (music * g).astype(np.float32)
    # Peak-normalize the sum and apply the SAME gain to the ground-truth
    # stems so sep_mix == sep_voice + sep_music exactly.
    mix = voice + music
    peak = float(np.max(np.abs(mix)))
    k = 0.95 / peak if peak > 0.95 else 1.0
    sf.write(FIXTURES_DIR / "sep_mix.wav", (mix * k).astype(np.float32), SR)
    sf.write(
        FIXTURES_DIR / "sep_music.wav",
        (music * k).astype(np.float32), SR,
    )
    sf.write(
        FIXTURES_DIR / "sep_voice.wav",
        (voice * k).astype(np.float32), SR,
    )
    spk = [[round(s / SR, 3), round(e / SR, 3)] for s, e in (seg1, seg2)]
    return {"duration_s": round(n / SR, 3), "speech_intervals": spk}


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

        # --- sep_*.wav: M7 separation fixture (stereo, music bed + speech)
        sep_info = _write_sep_fixture(speech)

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
            {"file": "sep_mix.wav", "duration_s": sep_info["duration_s"], "sample_rate": SR,
             "channels": 2, "speech_intervals": sep_info["speech_intervals"]},
            {"file": "sep_music.wav", "duration_s": sep_info["duration_s"], "sample_rate": SR,
             "channels": 2},
            {"file": "sep_voice.wav", "duration_s": sep_info["duration_s"], "sample_rate": SR,
             "channels": 2, "speech_intervals": sep_info["speech_intervals"]},
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
