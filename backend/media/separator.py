"""audio-separator engine adapter (M7 Task 1, SPEC §8/§9, argon_m7).

Every interaction with audio-separator 0.30.2 is isolated here:

- ``Separator`` is built with ``normalization_threshold=1.0`` and
  ``amplification_threshold=0.0`` — the default 0.9 peak-normalizes each
  window independently and causes gain jumps at crossfades.
- MDX keeps ``invert_using_spec=False`` (True triggers the upstream
  soundfile write bug — all-zero int16 output) and ``segment_size=256``
  (any other value silently switches to the onnx2torch/PyTorch path).
- Windows: ≤ ``_WINDOW_S`` s with ``_OVERLAP_S`` s raised-cosine crossfades
  via ``process_ola``. All-silent windows bypass the library entirely —
  ``prepare_mix``/``demix_demucs`` can raise or divide by zero on zeros.
- Each window is resampled 48 → 44.1 kHz (soxr VHQ) before separation and
  back after, padded/trimmed to the exact frame count. Mono input is
  duplicated to stereo for the model and averaged back to mono; stereo
  stays stereo.
- Output naming uses absolute ``custom_output_names`` — soundfile output
  ignores ``output_dir`` upstream, so stems would otherwise land in CWD.
- OOM ladder: demucs segment 7 → 4 → CPU; MDX GPU → CPU directly. OOM is
  detected by message substrings (torch ``OutOfMemoryError`` plus the ONNX
  Runtime / cuDNN allocation messages).
- CPU runs are forced by patching ``torch.cuda.is_available`` False during
  ``Separator`` construction so torch *and* ONNX Runtime pick CPU.
- ``download_file_if_not_exists`` is wrapped to emit
  ``model_downloading{model_id, pct}`` progress.

Heavy imports (torch, audio_separator) stay lazy inside functions.
"""
from __future__ import annotations

import contextlib
import gc
import os
import shutil
import unittest.mock as mock
from pathlib import Path
from typing import Callable, Literal

import numpy as np
import soundfile as sf
import soxr

from backend.core.messages import msg
from backend.core.models_manifest import model_dir
from backend.core.paths import TOOLS_DIR
from backend.media.chunks import process_ola
from backend.pipeline.types import CancelledError

Device = Literal["gpu", "cpu"]

SR = 48000
MODEL_SR = 44100
# 120 s windows: peak RSS stays ≈ baseline + ~0.25 GB of per-window stems;
# 300 s pushed a 20-min job to ~3.15 GiB RSS (plan bound is 3 GiB).
_WINDOW_S = 120.0
_OVERLAP_S = 3.0
_SILENCE_EPS = 1e-5

MODEL_FILES = {"htdemucs": "htdemucs.yaml", "kim_vocal_2": "Kim_Vocal_2.onnx"}
_MODEL_IDS = {"htdemucs": "htdemucs", "kim_vocal_2": "kim_vocal_2"}
_DEMUCS_MUSIC_STEMS = ("Drums", "Bass", "Other")

ReportFn = Callable[..., None] | None
CancelFn = Callable[[], bool] | None


def _prepend_ffmpeg_dir() -> None:
    """The library shells to bare ``ffmpeg``/``ffprobe`` — put the bundled
    tools dir on PATH once (idempotent, no absolute paths needed)."""
    d = TOOLS_DIR / "ffmpeg" / "bin"
    if d.is_dir():
        path = os.environ.get("PATH", "")
        if str(d) not in path.split(os.pathsep):
            os.environ["PATH"] = str(d) + os.pathsep + path


def _is_oom(exc: BaseException) -> bool:
    try:
        import torch

        if isinstance(exc, torch.cuda.OutOfMemoryError):
            return True
    except Exception:
        pass
    text = str(exc).lower()
    return (
        "out of memory" in text
        or "cudnn_status_alloc_failed" in text
        or "failed to allocate" in text
    )


def _is_silent(block: np.ndarray) -> bool:
    return float(np.abs(block).max(initial=0.0)) < _SILENCE_EPS


def _write_model_input(block: np.ndarray, dst: Path) -> Path:
    """48 kHz ``(n, ch)`` block → 44.1 kHz float WAV for the model.

    Mono is duplicated to stereo so the library always sees 2 channels.
    """
    y = np.asarray(block, dtype=np.float32)
    if y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    y = soxr.resample(y, SR, MODEL_SR, quality="VHQ")
    sf.write(str(dst), y, MODEL_SR, subtype="FLOAT", format="WAV")
    return dst


def _read_stem(path: Path, n: int, channels: int) -> np.ndarray:
    """44.1 kHz stem file → 48 kHz ``(n, channels)`` float32, exact length."""
    y, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if sr != MODEL_SR:
        raise AssertionError(f"stem at {sr} Hz, expected {MODEL_SR}")
    y = soxr.resample(y, sr, SR, quality="VHQ")
    if len(y) >= n:
        y = y[:n]
    else:
        y = np.pad(y, ((0, n - len(y)), (0, 0)))
    if channels == 1:
        y = y.mean(axis=1, keepdims=True)
    elif y.shape[1] == 1:
        y = np.repeat(y, 2, axis=1)
    return np.asarray(y, dtype=np.float32)


def _sum_demucs_music(base: Path, dst: Path) -> Path:
    """drums+bass+other summed in float32 → the demucs music stem.

    (``mix − vocals`` is deliberately not used — it leaves ghost-vocal
    phase residuals per argon_m7.)
    """
    stems = [
        sf.read(str(base) + f"_{s[0].lower()}.wav",
                dtype="float32", always_2d=True)[0]
        for s in _DEMUCS_MUSIC_STEMS
    ]
    n = min(len(s) for s in stems)
    music = stems[0][:n] + stems[1][:n] + stems[2][:n]
    sf.write(str(dst), music, MODEL_SR, subtype="FLOAT", format="WAV")
    return dst


def _single_stem_for(model: str, need: set[str]) -> str | None:
    """``output_single_stem`` skips writing stems we never read."""
    if "voice" not in need:
        return "Instrumental" if model != "htdemucs" else None
    if "music" not in need:
        return "Vocals"
    return None


def _new_separator(model: str, device: Device, need: set[str],
                   demucs_segment: int):
    """Construct a ``Separator`` honouring every argon_m7 constraint."""
    from audio_separator.separator import Separator  # noqa: E401 — lazy

    _prepend_ffmpeg_dir()
    kw: dict = {
        "model_file_dir": str(model_dir(_MODEL_IDS[model])),
        "output_dir": str(model_dir(_MODEL_IDS[model])),  # unused; safe dir
        "output_format": "WAV",
        "log_level": 50,
        "use_soundfile": True,
        "sample_rate": MODEL_SR,
        "normalization_threshold": 1.0,
        "amplification_threshold": 0.0,
        "invert_using_spec": False,
    }
    single = _single_stem_for(model, need)
    if single is not None:
        kw["output_single_stem"] = single
    if model == "htdemucs":
        kw["demucs_params"] = {
            "segment_size": demucs_segment,
            "shifts": 0,
            "overlap": 0.25,
            "segments_enabled": True,
        }
    else:
        kw["mdx_params"] = {
            "hop_length": 1024,
            "segment_size": 256,  # anything else → onnx2torch, never use
            "overlap": 0.25,
            "batch_size": 1,
            "enable_denoise": False,
        }
    ctx = (
        mock.patch("torch.cuda.is_available", lambda: False)
        if device == "cpu"
        else contextlib.nullcontext()
    )
    if device == "gpu":
        # Pin deterministic kernels: the demucs conv stack otherwise picks
        # per-process cuDNN algorithms and occasional runs emit a constant
        # low-frequency rumble into the vocals stem (observed ~1-in-2 runs).
        try:
            import torch

            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
        except Exception:
            pass
    with ctx:
        return Separator(**kw)


def _hook_download_progress(sep, model: str, report: ReportFn,
                            should_cancel: CancelFn) -> None:
    """Wrap ``download_file_if_not_exists`` so real downloads emit
    ``model_downloading{model_id, pct}`` progress rows."""
    if report is None:
        return
    cancel = should_cancel or (lambda: False)

    def _download(url: str, output_path: str) -> None:
        if os.path.isfile(output_path):
            return
        import requests

        model_id = os.path.basename(str(output_path))
        resp = requests.get(url, stream=True, timeout=300)
        if resp.status_code != 200:
            raise RuntimeError(
                f"failed to download model file {model_id}:"
                f" HTTP {resp.status_code}"
            )
        total = int(resp.headers.get("content-length", 0) or 0)
        done = 0
        try:
            with open(output_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    if cancel():
                        raise CancelledError()
                    f.write(chunk)
                    done += len(chunk)
                    pct = int(done * 100 / total) if total else 0
                    report(
                        None,
                        *msg("model_downloading", model_id=model_id, pct=pct),
                    )
        except BaseException:
            Path(output_path).unlink(missing_ok=True)
            raise

    sep.download_file_if_not_exists = _download


class _Engine:
    """One ``Separator`` with its model loaded, on one device/rung."""

    def __init__(self, model: str, device: Device, need: set[str],
                 demucs_segment: int, report: ReportFn = None,
                 should_cancel: CancelFn = None) -> None:
        self.model = model
        self.device = device
        self.need = set(need)
        self.sep = _new_separator(model, device, need, demucs_segment)
        _hook_download_progress(self.sep, model, report, should_cancel)
        self.sep.load_model(MODEL_FILES[model])
        self._patch_stem_writer()

    def _patch_stem_writer(self) -> None:
        """Upstream ``write_audio_soundfile`` only handles F-contiguous
        stems — a C-order (N,2) source is interleaved into an int16 mono
        buffer so float audio becomes zeros (argon_m7, confirmed on MDX).
        Replace it with a layout-safe soundfile write (FLOAT WAV)."""
        def _write(stem_path, stem_source) -> None:
            Path(stem_path).parent.mkdir(parents=True, exist_ok=True)
            sf.write(
                str(stem_path),
                np.ascontiguousarray(
                    np.asarray(stem_source, dtype=np.float32)
                ),
                MODEL_SR,
                subtype="FLOAT",
                format="WAV",
            )

        if self.sep.model_instance is not None:
            self.sep.model_instance.write_audio_soundfile = _write

    def separate_window(self, win_in: Path, wdir: Path) -> dict:
        """Separate one 44.1 kHz window; returns stem paths per role."""
        base = wdir / f"{win_in.stem}"
        names = {
            "Vocals": f"{base}_v",
            "Instrumental": f"{base}_i",
            "Drums": f"{base}_d",
            "Bass": f"{base}_b",
            "Other": f"{base}_o",
        }
        self.sep.separate(str(win_in), custom_output_names=names)
        out: dict[str, Path] = {}
        if "voice" in self.need:
            out["voice"] = Path(f"{base}_v.wav")
        if "music" in self.need:
            if self.model == "htdemucs":
                out["music"] = _sum_demucs_music(base, Path(f"{base}_m.wav"))
            else:
                out["music"] = Path(f"{base}_i.wav")
        return out

    def close(self) -> None:
        sep = getattr(self, "sep", None)
        if sep is not None:
            sep.model_instance = None
            self.sep = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def _make_engine(model: str, device: Device, need: set[str],
                 demucs_segment: int, report: ReportFn = None,
                 should_cancel: CancelFn = None) -> _Engine:
    return _Engine(model, device, need, demucs_segment, report, should_cancel)


def _ladder(model: str, device: Device) -> list[tuple[Device, int]]:
    """(device, segment) rungs. Demucs: gpu 7 → gpu 4 → cpu. MDX: gpu → cpu
    (the segment-128 middle rung is broken upstream and forbidden)."""
    if device == "cpu":
        return [("cpu", 256 if model != "htdemucs" else 7)]
    if model == "htdemucs":
        return [("gpu", 7), ("gpu", 4), ("cpu", 7)]
    return [("gpu", 256), ("cpu", 256)]


def _run_attempt(
    in_wav: Path,
    voice_wav: Path | None,
    music_wav: Path | None,
    model: str,
    device: Device,
    demucs_segment: int,
    need: set[str],
    work_dir: Path,
    report: ReportFn,
    should_cancel: CancelFn,
    window_s: float,
    overlap_s: float,
) -> None:
    stems_dir = work_dir / "sep_stems"
    shutil.rmtree(stems_dir, ignore_errors=True)
    stems_dir.mkdir(parents=True, exist_ok=True)
    cancel = should_cancel or (lambda: False)
    emit = report or (lambda *a: None)
    engine = _make_engine(
        model, device, need, demucs_segment, emit, should_cancel
    )
    both = voice_wav is not None and music_wav is not None
    chunk = int(window_s * SR)
    overlap = int(overlap_s * SR)
    try:
        widx = iter(range(1_000_000))

        def fn(block: np.ndarray) -> np.ndarray:
            i = next(widx)
            if cancel():
                raise CancelledError()
            n, ch = block.shape
            if _is_silent(block):
                voice = block
                music = np.zeros_like(block)
            else:
                win_in = _write_model_input(
                    block, stems_dir / f"win_{i:05d}_in.wav"
                )
                stems = engine.separate_window(win_in, stems_dir)
                voice = music = None
                if "voice" in need:
                    voice = _read_stem(stems["voice"], n, ch)
                if "music" in need:
                    music = _read_stem(stems["music"], n, ch)
            if both:
                # cache the music stem for the second stitch pass
                sf.write(
                    str(stems_dir / f"win_{i:05d}_m48.wav"),
                    music, SR, subtype="FLOAT",
                )
                return voice
            return voice if voice_wav is not None else music

        main = voice_wav if voice_wav is not None else music_wav
        span = 0.85 if both else 1.0
        process_ola(
            in_wav, main, chunk, overlap, fn, should_cancel,
            lambda p: emit(span * p),
        )
        if both:
            widx2 = iter(range(1_000_000))

            def fn2(block: np.ndarray) -> np.ndarray:
                i = next(widx2)
                if cancel():
                    raise CancelledError()
                cached = stems_dir / f"win_{i:05d}_m48.wav"
                y, _ = sf.read(
                    str(cached), dtype="float32", always_2d=True
                )
                return y

            process_ola(
                in_wav, music_wav, chunk, overlap, fn2, should_cancel,
                lambda p: emit(0.85 + 0.15 * p),
            )
    finally:
        engine.close()


def process_file(
    in_wav: Path,
    voice_wav: Path | None,
    music_wav: Path | None,
    model: str,
    device: Device,
    work_dir: Path,
    report: ReportFn = None,
    should_cancel: CancelFn = None,
    window_s: float = _WINDOW_S,
    overlap_s: float = _OVERLAP_S,
) -> Device:
    """Separate ``in_wav`` (48 kHz float WAV) into voice/music stems.

    ``voice_wav``/``music_wav`` select which 48 kHz stem files to write (at
    least one required). Returns the device actually used — ``"cpu"`` when
    the GPU OOM ladder forced the SPEC §9 whole-file retry.
    """
    if voice_wav is None and music_wav is None:
        raise ValueError("process_file needs voice_wav and/or music_wav")
    if model not in MODEL_FILES:
        raise ValueError(f"unknown separation model: {model}")
    if sf.info(str(in_wav)).samplerate != SR:
        raise AssertionError(
            f"separate needs 48 kHz input, got"
            f" {sf.info(str(in_wav)).samplerate}"
        )
    need = set()
    if voice_wav is not None:
        need.add("voice")
    if music_wav is not None:
        need.add("music")
    exc0: BaseException | None = None
    for dev, seg in _ladder(model, device):
        try:
            _run_attempt(
                in_wav, voice_wav, music_wav, model, dev, seg, need,
                work_dir, report, should_cancel, window_s, overlap_s,
            )
            return dev
        except BaseException as exc:
            if not _is_oom(exc):
                raise
            exc0 = exc  # engine already freed in _run_attempt's finally
    raise exc0  # every rung OOMed — report the last error honestly
