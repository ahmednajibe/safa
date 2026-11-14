#!/usr/bin/env python
"""M1 feasibility gate: prove every engine runs on CPU and (where supported) GPU.

Run:  uv run python scripts/feasibility.py
Writes docs/feasibility_results.json, prints a table.
Exit code 0 iff every CPU check passed (GPU failures are recorded, not fatal).
"""
from __future__ import annotations

import contextlib
import gc
import json
import os
import re
import subprocess
import sys
import time
import unittest.mock as mock
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import soxr  # noqa: E402
import torch  # noqa: E402

from backend.core.ffmpeg import ffmpeg_version, has_encoder  # noqa: E402
from backend.core.gpu import VramSampler, gpu_info, setup_cuda_dlls  # noqa: E402
from backend.core.models_manifest import configure_model_env, model_dir  # noqa: E402
from backend.core.paths import DATA_DIR, FIXTURES_DIR  # noqa: E402

SR = 48000
SPEECH = FIXTURES_DIR / "speech.wav"
SPEECH_NOISE = FIXTURES_DIR / "speech_noise.wav"
SPEECH_MUSIC = FIXTURES_DIR / "speech_music.wav"
SPEECH_SILENCES = FIXTURES_DIR / "speech_silences.wav"
WORK_DIR = DATA_DIR / "feasibility"


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


def _fixture_meta(filename: str) -> dict:
    meta = json.loads((FIXTURES_DIR / "fixtures.json").read_text(encoding="utf-8"))
    return next(e for e in meta["files"] if e["file"] == filename)


def _cleanup_torch() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# ---------------------------------------------------------------- checks


def check_ffmpeg() -> str:
    nvenc = has_encoder("h264_nvenc")
    return f"{ffmpeg_version()}; h264_nvenc={'yes' if nvenc else 'NO'}"


def check_torch_cpu() -> str:
    a = torch.randn(512, 512)
    float((a @ a).sum())
    return f"torch {torch.__version__} cpu matmul"


def check_torch_gpu() -> str:
    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() is False")
    a = torch.randn(1024, 1024, device="cuda")
    float((a @ a).sum())
    return f"{torch.cuda.get_device_name(0)}; alloc={torch.cuda.memory_allocated() // 2**20}MB"


def _dfn_enhance(device: str) -> str:
    try:
        from df.enhance import enhance, init_df
    except Exception:
        # deepfilternet 0.5.6 imports torchaudio.backend.common.AudioMetaData;
        # shim only needed if the import actually fails (SPEC §8).
        import types
        import torchaudio

        common = types.ModuleType("torchaudio.backend.common")
        common.AudioMetaData = torchaudio.AudioMetaData
        sys.modules["torchaudio.backend.common"] = common
        from df.enhance import enhance, init_df

    # Keep the pretrained download under models/: init_df() -> maybe_download_model
    # uses df.enhance.get_cache_dir (appdirs user cache). Redirect it to our
    # manifest dir; DeepFilterNet3 lands at models/deepfilternet/DeepFilterNet3/.
    mod = sys.modules["df.enhance"]
    orig_get_cache_dir = mod.get_cache_dir
    mod.get_cache_dir = lambda: str(model_dir("deepfilternet3"))
    ctx = (
        mock.patch("torch.cuda.is_available", lambda: False)
        if device == "cpu"
        else contextlib.nullcontext()
    )
    try:
        with ctx:
            # enhance() also resolves get_device() internally — keep the CPU
            # patch active for init + inference.
            model, df_state, _ = init_df(log_level="ERROR")
            model = model.to(device).eval()
            audio_np, sr = sf.read(str(SPEECH_NOISE), dtype="float32")
            audio = torch.from_numpy(audio_np)[None, :]  # enhance wants [C, T]
            with torch.no_grad():
                # enhance() calls audio.numpy() then moves features to the
                # model's device internally — input tensor stays on CPU.
                out = enhance(model, df_state, audio, atten_lim_db=24)
    finally:
        mod.get_cache_dir = orig_get_cache_dir
    out = out.squeeze().cpu().numpy().astype(np.float32)
    del model, df_state
    n0 = int(0.4 * sr)  # fixture has a 0.5 s noise-only head
    rms_in, rms_out = _rms(audio_np[:n0]), _rms(out[:n0])
    assert abs(len(out) - len(audio_np)) <= 0.01 * len(audio_np), (
        f"len {len(out)} vs input {len(audio_np)}"
    )
    assert rms_out < rms_in, f"noise head RMS not reduced: {rms_out:.4f} >= {rms_in:.4f}"
    return f"noise-head RMS {rms_in:.4f} -> {rms_out:.4f}; len ok"


def check_dfn_cpu() -> str:
    return _dfn_enhance("cpu")


def check_dfn_gpu() -> str:
    return _dfn_enhance("cuda")


def _make_separator(device: str, param_key: str, params: dict, out_dir: Path):
    from audio_separator.separator import Separator

    kw = dict(
        model_file_dir=str(model_dir("htdemucs")),
        output_dir=str(out_dir),
        output_format="WAV",
        log_level=50,
        use_soundfile=True,
    )
    kw[param_key] = params
    if device == "cpu":
        with mock.patch("torch.cuda.is_available", lambda: False):
            sep = Separator(**kw)
    else:
        sep = Separator(**kw)
    return sep


def _sep_outputs_ok(outs: list[str]) -> tuple[bool, str]:
    names = [Path(o).name for o in outs]
    has_vocals = any("vocal" in n.lower() for n in names)
    return has_vocals and len(outs) >= 2, ", ".join(names)


def _separate_into(sep, out_dir: Path, audio: Path) -> list[str]:
    """audio-separator 0.30.2 bug: write_audio_soundfile ignores output_dir
    (only the pydub writer joins it). chdir so relative stem paths land in
    out_dir. Returns absolute paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cwd = os.getcwd()
    os.chdir(out_dir)
    try:
        outs = sep.separate(str(audio))
    finally:
        os.chdir(cwd)
    return [o if os.path.isabs(o) else str(out_dir / o) for o in outs]


def _demucs_run(device: str, segment: int, out_dir: Path) -> str:
    sep = _make_separator(
        device,
        "demucs_params",
        {"segment_size": segment, "shifts": 0, "overlap": 0.25, "segments_enabled": True},
        out_dir,
    )
    sep.load_model("htdemucs.yaml")
    outs = _separate_into(sep, out_dir, SPEECH_MUSIC)
    ok, names = _sep_outputs_ok(outs)
    assert ok, f"expected vocals+instrumental stems, got: {names}"
    return f"segment={segment} stems: {names}"


def check_demucs_cpu() -> str:
    return _demucs_run("cpu", 7, WORK_DIR / "demucs_cpu")


def check_demucs_gpu() -> str:
    try:
        return _demucs_run("cuda", 7, WORK_DIR / "demucs_gpu")
    except torch.cuda.OutOfMemoryError:
        _cleanup_torch()
        note = _demucs_run("cuda", 4, WORK_DIR / "demucs_gpu")
        return "OOM at segment=7, retried: " + note


def _mdx_run(device: str, out_dir: Path) -> str:
    sep = _make_separator(
        device,
        "mdx_params",
        {"hop_length": 1024, "segment_size": 256, "overlap": 0.25,
         "batch_size": 1, "enable_denoise": False},
        out_dir,
    )
    sep.load_model("Kim_Vocal_2.onnx")
    outs = _separate_into(sep, out_dir, SPEECH_MUSIC)
    provider = sep.onnx_execution_provider
    ok, names = _sep_outputs_ok(outs)
    assert ok, f"expected vocals stem, got: {names}"
    del sep
    return f"provider={provider} stems: {names}"


def check_mdx_cpu() -> str:
    return _mdx_run("cpu", WORK_DIR / "mdx_cpu")


def check_mdx_gpu() -> str:
    return _mdx_run("cuda", WORK_DIR / "mdx_gpu")


def check_silero_vad() -> str:
    from silero_vad import get_speech_timestamps, load_silero_vad

    audio_np, sr = sf.read(str(SPEECH_SILENCES), dtype="float32")
    mono16 = soxr.resample(audio_np, sr, 16000)
    model = load_silero_vad()
    ts = get_speech_timestamps(
        torch.from_numpy(mono16), model,
        sampling_rate=16000, return_seconds=True,
    )
    speech_iv = [[float(s["start"]), float(s["end"])] for s in ts]
    gaps = [[speech_iv[i][1], speech_iv[i + 1][0]] for i in range(len(speech_iv) - 1)]
    gaps = [g for g in gaps if g[1] - g[0] >= 1.5]
    want = _fixture_meta("speech_silences.wav")["silence_intervals"]
    assert len(gaps) == len(want), f"detected {len(gaps)} gaps {gaps}, want {want}"
    for g, w in zip(gaps, want):
        assert abs(g[0] - w[0]) <= 0.3 and abs(g[1] - w[1]) <= 0.3, (
            f"gap {g} vs recorded {w}"
        )
    return f"gaps {[[round(a,2), round(b,2)] for a,b in gaps]} match recorded {want}"


def check_pedalboard() -> str:
    from pedalboard import Compressor, HighpassFilter, PeakFilter, Pedalboard

    audio, sr = sf.read(str(SPEECH), dtype="float32")
    board = Pedalboard(
        [
            HighpassFilter(cutoff_frequency_hz=80.0),
            PeakFilter(cutoff_frequency_hz=4000.0, gain_db=3.0, q=0.7),
            Compressor(),
        ]
    )
    out = board(audio, sr)
    assert np.isfinite(out).all(), "non-finite output"
    assert len(out) == len(audio), f"len {len(out)} vs {len(audio)}"
    return f"chain ok, out RMS {_rms(out):.4f} (in {_rms(audio):.4f})"


def check_ffmpeg_deesser_loudnorm() -> str:
    from backend.core.ffmpeg import find_ffmpeg

    ffmpeg = str(find_ffmpeg())
    out_wav = WORK_DIR / "loudnorm_out.wav"
    out_wav.parent.mkdir(parents=True, exist_ok=True)

    def _run(args: list[str]) -> str:
        p = subprocess.run(args, capture_output=True, text=True)
        assert p.returncode == 0, f"ffmpeg rc={p.returncode}: {p.stderr[-600:]}"
        return p.stderr

    # pass 1: measure
    err = _run([ffmpeg, "-hide_banner", "-y", "-i", str(SPEECH),
                "-af", "deesser,loudnorm=I=-16:TP=-1:LRA=11:print_format=json",
                "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    assert m, "no loudnorm json in output"
    meas = {k.strip().strip('"'): v.strip().strip('",') for k, v in
            (line.split(":") for line in m.group(0).strip("{}").splitlines() if ":" in line)}
    # pass 2: apply with measured_* values
    af = (
        "deesser,"
        "loudnorm=I=-16:TP=-1:LRA=11"
        f":measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
        f":measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}"
        f":offset={meas['target_offset']}:linear=true"
    )
    _run([ffmpeg, "-hide_banner", "-y", "-i", str(SPEECH), "-af", af,
          "-ar", str(SR), str(out_wav)])
    # verify integrated loudness of result
    err = _run([ffmpeg, "-hide_banner", "-y", "-i", str(out_wav),
                "-af", "ebur128=peak=true", "-f", "null", "-"])
    # ebur128 prints per-frame "I:" lines, then the Summary block last.
    matches = re.findall(r"I:\s*(-?\d+\.?\d*)\s*LUFS", err)
    assert matches, f"no ebur128 I in output: {err[-400:]}"
    i_lufs = float(matches[-1])
    assert abs(i_lufs - (-16.0)) <= 1.0, f"integrated {i_lufs} LUFS not within ±1 of -16"
    return f"integrated {i_lufs} LUFS (target -16 ±1)"


def _whisper(device: str, compute: str) -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(
        "small",
        device=device,
        compute_type=compute,
        download_root=str(model_dir("whisper_small")),
    )
    segments, info = model.transcribe(str(SPEECH), beam_size=5)
    text = " ".join(s.text for s in segments).strip().lower()
    del model
    gc.collect()
    assert "quick" in text and "fox" in text, f"transcript missing words: {text[:160]!r}"
    return f"[{info.language} p={info.language_probability:.2f}] {text[:90]!r}"


def check_whisper_cpu() -> str:
    return _whisper("cpu", "int8")


def check_whisper_gpu() -> str:
    import ctranslate2

    types = ctranslate2.get_supported_compute_types("cuda")
    compute = "int8_float32" if "int8_float32" in types else "float16"
    return _whisper("cuda", compute) + f" (compute={compute})"


# ---------------------------------------------------------------- runner


@dataclass
class Check:
    name: str
    run_cpu: Callable[[], str]
    run_gpu: Callable[[], str] | None = None


CHECKS: list[Check] = [
    Check("ffmpeg", check_ffmpeg, None),
    Check("torch_cuda", check_torch_cpu, check_torch_gpu),
    Check("deepfilternet3", check_dfn_cpu, check_dfn_gpu),
    Check("demucs_htdemucs", check_demucs_cpu, check_demucs_gpu),
    Check("mdx_kim_vocal_2", check_mdx_cpu, check_mdx_gpu),
    Check("silero_vad", check_silero_vad, None),
    Check("pedalboard_chain", check_pedalboard, None),
    Check("ffmpeg_deesser_loudnorm", check_ffmpeg_deesser_loudnorm, None),
    Check("faster_whisper_small", check_whisper_cpu, check_whisper_gpu),
]


def main() -> int:
    configure_model_env()
    dll_dirs = setup_cuda_dlls()
    # audio-separator / pydub shell out to bare `ffmpeg` — tools dir first.
    ffbin = str(ROOT / "tools" / "ffmpeg" / "bin")
    os.environ["PATH"] = ffbin + os.pathsep + os.environ.get("PATH", "")
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    info = gpu_info()
    print(f"GPU: {info if info else 'none'}; cuda_dll_dirs={len(dll_dirs)}")
    print(f"{'check':26} {'dev':4} {'ok':3} {'sec':>7} {'vram+MB':>8}  note")
    print("-" * 100)

    results: list[dict] = []
    for chk in CHECKS:
        for device, fn in (("cpu", chk.run_cpu), ("gpu", chk.run_gpu)):
            if fn is None:
                continue
            t0 = time.perf_counter()
            with VramSampler() as vs:
                if device == "gpu" and not torch.cuda.is_available():
                    ok, note = False, "no CUDA device"
                else:
                    try:
                        note = fn() or "ok"
                        ok = True
                    except (Exception, SystemExit) as e:  # record & continue
                        note = f"{type(e).__name__}: {e}"[:400]
                        ok = False
            secs = round(time.perf_counter() - t0, 2)
            _cleanup_torch()
            row = {
                "name": chk.name, "device": device, "ok": ok,
                "seconds": secs, "peak_vram_mb": vs.peak_mb, "note": note,
            }
            results.append(row)
            print(
                f"{chk.name:26} {device:4} {str(ok):3} {secs:>7} "
                f"{vs.peak_mb:>8}  {note[:70]}"
            )

    out = ROOT / "docs" / "feasibility_results.json"
    payload = {
        "machine": {"gpu": info, "torch": torch.__version__,
                    "cuda_dll_dirs": [str(d) for d in dll_dirs]},
        "results": results,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    cpu_ok = all(r["ok"] for r in results if r["device"] == "cpu")
    print("CPU checks:", "ALL OK" if cpu_ok else "FAILURES PRESENT")
    return 0 if cpu_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
