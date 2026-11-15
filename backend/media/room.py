"""Room-echo helpers (M6 Task 2): resonance finding + light WPE + notch EQ.

Port of the user-approved experiment
``.orchestrator/analysis/try_wpe.py`` (DFN 24 dB → WPE → auto-resonance EQ
on Recording (3), variant F — the output the user called "very good now"):

- ``wpe`` — single-channel WPE dereverb (complex64 STFT, taps/delay/iters
  identical to the experiment). Callers chunk to ≤ 20 s / 2 s overlap.
- ``find_room_resonances`` — narrow 220–1000 Hz peaks of the Welch
  spectrum (nperseg 4096) over the loud 40 % of 10 ms frames, ranked by
  prominence over a 15-bin median baseline.
- ``apply_cuts`` — pedalboard ``PeakFilter`` notches, depth = prominence
  capped by the op's strength (q 4.0).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.ndimage import median_filter

from backend.pipeline.types import CancelledError

SR = 48000
_FRAME = 480  # 10 ms energy frames for the loud-frame mask
_BLOCK = 1 << 20


def wpe(
    x: np.ndarray,
    taps: int = 10,
    delay: int = 3,
    iters: int = 3,
    nfft: int = 1024,
    hop: int = 256,
) -> np.ndarray:
    """WPE dereverb on one float32 channel — verbatim port of
    try_wpe.py (complex64, same STFT grid). Returns same length as ``x``.

    The caller chunks to ≤ 20 s / 2 s overlap — each block's complex
    buffers stay ≈150 MB and the crossfade hides the blockwise reverb
    estimate change.
    """
    y = np.asarray(x, dtype=np.float32)
    _, _, Y = signal.stft(
        y, fs=SR, nperseg=nfft, noverlap=nfft - hop, boundary="even"
    )
    Y = Y.astype(np.complex64)  # (F, T)
    F, T = Y.shape
    Yt = np.zeros((F, T, taps), np.complex64)  # delayed taps
    for k in range(taps):
        s = delay + k
        Yt[:, s:, k] = Y[:, : T - s]
    D = Y.copy()
    eye = np.eye(taps, dtype=np.complex64)[None] * 1e-6
    for _ in range(iters):
        lam = np.maximum(np.abs(D) ** 2, 1e-10).astype(np.float32)
        lam = median_filter(lam, size=(1, 3))  # light temporal smoothing
        W = Yt / lam[..., None]
        R = np.einsum("ftk,ftl->fkl", W, Yt.conj()) + eye
        r = np.einsum("ftk,ft->fk", W, Y.conj())
        g = np.linalg.solve(R, r[..., None])[..., 0]
        D = Y - np.einsum("ftk,fk->ft", Yt, g.conj())
    _, d = signal.istft(
        D, fs=SR, nperseg=nfft, noverlap=nfft - hop, boundary=True
    )
    d = d[: len(y)]
    return np.pad(d, (0, len(y) - len(d))).astype(np.float32)


def _loud_frames(
    wav: Path, report=None, should_cancel=None
) -> np.ndarray:
    """Concatenated 10 ms mono frames with energy above the 60th
    percentile — the voiced/loud audio resonances live on."""
    info = sf.info(str(wav))
    n = info.frames
    nframes = n // _FRAME
    if nframes == 0:
        return np.zeros(0, dtype=np.float32)
    energy = np.empty(nframes, dtype=np.float64)
    with sf.SoundFile(str(wav), "r") as fin:
        pos = 0
        while pos < nframes * _FRAME:
            if should_cancel and should_cancel():
                raise CancelledError()
            block = fin.read(
                min(_BLOCK, nframes * _FRAME - pos),
                dtype="float32",
                always_2d=True,
            )
            if len(block) == 0:
                break
            mono = block.mean(axis=1)
            k = len(mono) // _FRAME
            fr = mono[: k * _FRAME].reshape(k, _FRAME)
            i0 = pos // _FRAME
            energy[i0:i0 + k] = (fr ** 2).mean(axis=1)
            pos += k * _FRAME
    thresh = np.percentile(energy, 60)
    keepmask = energy > thresh
    nkeep = int(keepmask.sum())
    if nkeep == 0:
        return np.zeros(0, dtype=np.float32)
    kept = np.empty(nkeep * _FRAME, dtype=np.float32)
    with sf.SoundFile(str(wav), "r") as fin:
        pos = 0
        out = 0
        while pos < nframes * _FRAME:
            if should_cancel and should_cancel():
                raise CancelledError()
            block = fin.read(
                min(_BLOCK, nframes * _FRAME - pos),
                dtype="float32",
                always_2d=True,
            )
            if len(block) == 0:
                break
            mono = block.mean(axis=1)
            k = len(mono) // _FRAME
            fr = mono[: k * _FRAME].reshape(k, _FRAME)
            i0 = pos // _FRAME
            sel = fr[keepmask[i0:i0 + k]]
            kept[out:out + sel.size] = sel.ravel()
            out += sel.size
            pos += k * _FRAME
    if report:
        report(1.0)
    return kept[:out]


def find_room_resonances(
    wav: Path,
    fmin: float = 220.0,
    fmax: float = 1000.0,
    max_cuts: int = 3,
    min_prominence_db: float = 2.5,
    report=None,
    should_cancel=None,
) -> list[tuple[float, float]]:
    """``[(freq_hz, prominence_db)]`` — narrow peaks of the loud-frame
    Welch spectrum (nperseg 4096) standing ≥ ``min_prominence_db`` above a
    15-bin median baseline in [fmin, fmax], top ``max_cuts`` by
    prominence. Sorted by frequency for a stable report.
    """
    voiced = _loud_frames(wav, should_cancel=should_cancel)
    if should_cancel and should_cancel():
        raise CancelledError()
    emit = report or (lambda p: None)
    if len(voiced) < 4096:
        emit(1.0)
        return []
    f, P = signal.welch(voiced, fs=SR, nperseg=4096)
    db = 10 * np.log10(P + 1e-20)
    base = median_filter(db, size=15)
    resid = db - base
    idx = np.where((f >= fmin) & (f <= fmax))[0]
    peaks, props = signal.find_peaks(
        resid[idx], prominence=min_prominence_db
    )
    if len(peaks) == 0:
        emit(1.0)
        return []
    order = np.argsort(props["prominences"])[::-1][:max_cuts]
    cuts = [
        (float(f[idx][peaks[o]]), float(props["prominences"][o]))
        for o in order
    ]
    cuts.sort(key=lambda c: c[0])
    emit(1.0)
    return cuts


def apply_cuts(
    x: np.ndarray,
    sr: int,
    cuts: list[tuple[float, float]],
    max_cut_db: float,
    q: float = 4.0,
) -> np.ndarray:
    """Notch every ``(freq, prominence)`` by ``min(max_cut_db,
    prominence)`` dB at q — exactly the experiment's auto-EQ. ``x`` is
    (T,) or (T, C) float32; same shape returned. No cuts → input."""
    import pedalboard as pb

    a = np.asarray(x, dtype=np.float32)
    if len(cuts) == 0:
        return a
    board = pb.Pedalboard(
        [
            pb.PeakFilter(
                cutoff_frequency_hz=float(fr),
                gain_db=-float(min(max_cut_db, prom)),
                q=float(q),
            )
            for fr, prom in cuts
        ]
    )
    one = a.ndim == 1
    mat = a[None, :] if one else a.T
    res = board(mat, sr).astype(np.float32)
    return res[0] if one else res.T
