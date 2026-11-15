"""M6 Task 2: the ``dereverb`` op — room-resonance notch EQ + optional WPE.

The reference recipe is the user-approved experiment variant F
(DFN -> WPE -> auto-resonance EQ on Recording (3)): detection finds narrow
220-1000 Hz peaks over a median baseline, cuts are prominence-capped by
strength (gentle 3 / normal 4.5 / strong 6 dB), and WPE runs in <= 20 s
blocks with 2 s crossfades.
"""
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from backend.operations.base import OpContext
from backend.operations.impl import IMPLEMENTATIONS
from backend.operations.registry import OPERATIONS, ops_in_order

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH = GENERATED / "speech.wav"
SPEECH_SILENCES = GENERATED / "speech_silences.wav"
SR = 48000


def _dur(path: Path) -> float:
    i = sf.info(str(path))
    return i.frames / i.samplerate


def _ctx(tmp_path: Path, in_wav: Path, device="cpu"):
    info = sf.info(str(in_wav))
    return OpContext(
        work_dir=tmp_path,
        device=device,
        duration_s=info.frames / info.samplerate,
        sample_rate=info.samplerate,
        channels=info.channels,
        report=lambda *a: None,
        should_cancel=lambda: False,
    )


def _run(in_wav: Path, tmp_path: Path, params=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return IMPLEMENTATIONS["dereverb"](
        in_wav, params or {}, _ctx(tmp_path, in_wav)
    )


def _resonance_fixture(path: Path, freq=340.0, amp=0.08) -> Path:
    """speech_silences + a steady narrowband ring at ``freq`` — a standing
    room resonance sitting on real-ish voiced content."""
    x, sr = sf.read(str(SPEECH_SILENCES), dtype="float32")
    t = np.arange(len(x)) / sr
    ring = amp * np.sin(2 * np.pi * freq * t).astype(np.float32)
    sf.write(str(path), (x + ring).astype(np.float32), sr, subtype="FLOAT")
    return path


def _tone_db(wav: Path, freq: float) -> float:
    """Welch level (dB) of the bin nearest ``freq`` — how deep the notch cut."""
    from scipy import signal

    x, sr = sf.read(str(wav), dtype="float32")
    f, P = signal.welch(x, fs=sr, nperseg=4096)
    db = 10 * np.log10(P + 1e-20)
    return float(db[np.argmin(np.abs(f - freq))])


# ---- registry / contract -------------------------------------------------------


def test_registry_order_and_device():
    spec = OPERATIONS["dereverb"]
    assert spec.order == 42
    assert spec.planned is False
    assert spec.device == "cpu"
    ids = [op.id for op in ops_in_order()]
    assert ids.index("denoise") < ids.index("dereverb") < ids.index(
        "enhance"
    )


# ---- resonance detection ---------------------------------------------------------


def test_finds_synthetic_resonance(tmp_path):
    from backend.media.room import find_room_resonances

    src = _resonance_fixture(tmp_path / "reso.wav", freq=340.0)
    cuts = find_room_resonances(src)
    freqs = [f for f, _ in cuts]
    assert any(abs(f - 340.0) < 20 for f in freqs), freqs


def test_no_resonance_reports_empty_and_passthrough(tmp_path):
    """Flat-spectrum input (no peaks >= 2.5 dB) -> freqs: [] and output
    ~identical to input when WPE is off. Needs >= ~10 s: shorter white
    noise has too few Welch segments and its own ripple passes 2.5 dB."""
    rng = np.random.default_rng(2)
    flat = tmp_path / "flat.wav"
    sf.write(
        str(flat),
        (0.02 * rng.standard_normal(12 * SR)).astype(np.float32),
        SR,
        subtype="FLOAT",
    )
    res = _run(flat, tmp_path, {"wpe": "off"})
    assert res.message_code == "room_resonances_cut"
    assert res.message_params["freqs"] == []
    x, _ = sf.read(str(flat), dtype="float32")
    y, _ = sf.read(str(res.path), dtype="float32")
    np.testing.assert_allclose(y, x, atol=1e-5)


def test_strength_maps_to_max_cut(tmp_path):
    """A loud resonance is cut by exactly the strength cap (3/4.5/6 dB)."""
    src = _resonance_fixture(tmp_path / "reso.wav", freq=340.0, amp=0.15)
    before = _tone_db(src, 340.0)
    expected = {"gentle": 3.0, "normal": 4.5, "strong": 6.0}
    for strength, cap in expected.items():
        res = _run(
            src, tmp_path / strength, {"strength": strength, "wpe": "off"}
        )
        drop = before - _tone_db(res.path, 340.0)
        assert drop == pytest.approx(cap, abs=1.2), (
            f"{strength}: cut {drop:.1f} dB, cap {cap}"
        )
        assert res.message_code == "room_resonances_cut"
        assert any(abs(f - 340) < 20 for f in res.message_params["freqs"])
        assert _dur(res.path) == pytest.approx(_dur(src), abs=0.001)


# ---- WPE -----------------------------------------------------------------------


def _sine_speech(path: Path, seconds: float) -> Path:
    x, sr = sf.read(str(SPEECH), dtype="float32")
    n = int(seconds * SR)
    loop = np.resize(x, n) * 0.5
    t = np.arange(n) / SR
    loop += 0.15 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    sf.write(str(path), loop.astype(np.float32), SR, subtype="FLOAT")
    return path


def test_wpe_seams_bounded(tmp_path):
    """70 s through WPE (4 x <=20 s blocks, 2 s crossfades): sample jumps at
    block boundaries <= 1.5x the max jump elsewhere."""
    src = _sine_speech(tmp_path / "long70.wav", 70.0)
    res = _run(src, tmp_path, {"strength": "gentle", "wpe": "on"})
    y, sr = sf.read(str(res.path), dtype="float32", always_2d=True)
    y = y.mean(axis=1)
    chunk, hop = int(20 * sr), int(18 * sr)
    seams = [a for a in range(hop, len(y), hop)]
    d = np.abs(np.diff(y))
    mask = np.zeros(len(d), bool)
    for c in seams:
        mask[max(0, c - int(0.005 * sr)):c + int(0.005 * sr)] = True
    inside = d[mask].max()
    outside = d[~mask].max()
    assert inside <= 1.5 * outside, (
        f"seam jump {inside:.5f} > 1.5x elsewhere {outside:.5f}"
    )
    assert _dur(res.path) == pytest.approx(70.0, abs=0.001)


def test_wpe_length_and_stereo(tmp_path):
    """WPE keeps channel count and exact length on stereo input."""
    src = GENERATED / "speech_music.wav"
    res = _run(src, tmp_path, {"wpe": "on", "strength": "gentle"})
    info = sf.info(str(res.path))
    assert info.channels == 2
    assert info.frames == sf.info(str(src)).frames


# ---- real recording --------------------------------------------------------------


def test_recording3_dereverb_metrics(tmp_path, real_dir):
    """Recording (3): cuts land in 250-700 Hz (the room's measured modes),
    LTAS ripple improves >= 15 %, SRMR proxy drops < 0.5 dB, exact length."""
    from backend.media.decode import decode_to_wav

    from tests.audio_metrics import (
        ltas_ripple_db,
        load_mono,
        srmr_proxy,
        tail_energy_ratio,
    )

    src = real_dir / "Recording (3).m4a"
    in_wav = tmp_path / "in.wav"
    decode_to_wav(src, in_wav, 0)
    res = _run(in_wav, tmp_path, {"strength": "normal", "wpe": "on"})

    freqs = res.message_params["freqs"]
    assert freqs, "expected at least one resonance cut on Recording (3)"
    assert all(250 <= f <= 700 for f in freqs), freqs

    x, _ = load_mono(in_wav)
    y, _ = load_mono(res.path)
    rip_in, rip_out = ltas_ripple_db(x, SR), ltas_ripple_db(y, SR)
    assert rip_out <= 0.85 * rip_in, (
        f"ripple {rip_in:.2f} -> {rip_out:.2f} dB (<15% improvement)"
    )
    srmr_in, srmr_out = srmr_proxy(x, SR), srmr_proxy(y, SR)
    assert srmr_out >= srmr_in - 0.5, (
        f"srmr {srmr_in:.2f} -> {srmr_out:.2f} dB"
    )
    # R1 weak test: reverb energy lives in the ring-out tail — WPE must
    # lower the tail's share of total energy (an identity passthrough
    # leaves it unchanged).
    tail_in, tail_out = tail_energy_ratio(x, SR), tail_energy_ratio(y, SR)
    assert tail_out < tail_in, (
        f"tail energy ratio {tail_in:.3f} -> {tail_out:.3f} (no decrease)"
    )
    assert _dur(res.path) == pytest.approx(_dur(in_wav), abs=0.001)
