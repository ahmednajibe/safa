"""Disposable faster-whisper subprocess (``python -m backend.media.transcribe_child``).

faster-whisper 1.1.1 + CTranslate2 on Windows aborts the whole process
when a CUDA WhisperModel is torn down — ``del model`` or interpreter exit
lands in the ``cuda_malloc_async`` teardown and calls ``abort()``
(SYSTRAN/faster-whisper#1293). It is not a Python exception and cannot be
caught, and ``unload_model(to_cpu=True)`` does not fix it.

So every whisper attempt runs here, in a disposable child that:

- loads the model, transcribes, streams progress over stdout,
- prints one ``{"type": "result", ...}`` JSON line when done,
- exits via ``os._exit`` — C++ destructors never run, so the teardown
  crash is structurally impossible. OS reclaims the VRAM.

A mid-run crash is confined to this process; the parent sees a dead pipe
and falls back to CPU, which is strictly more coverage than the in-process
OOM ladder.

stdout is a JSON-lines protocol — one object per line::

    {"type": "progress", "value": 0.42}
    {"type": "message", "text": "...", "code": "language_detected",
     "params": {...}}
    {"type": "result", "language": "en", "language_prob": 0.97,
     "segments": [{"start": .., "end": ..,
                   "words": [{"start": .., "end": .., "text": ..}]}]}
    {"type": "error", "kind": "oom"|"other", "detail": "..."}

Everything else (HF/tqdm download bars, warnings) stays on stderr.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# argon_m8 Q1/Q2 — identical to the parent module's parameter set; kept
# here so the child is self-contained (the parent never imports whisper).
_VAD_PARAMETERS = {
    "threshold": 0.5,
    "min_silence_duration_ms": 500,
    "speech_pad_ms": 200,
}
_APPEND_PUNCT = "\"'.。,，!！?？:：”)]}、،؛؟»"   # default + ،؛؟»
_PREPEND_PUNCT = "\"'“¿([{-«"                  # default + «


def _emit(obj: dict) -> None:
    """One protocol line, flushed — os._exit skips interpreter flush."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _compute_type(device: str) -> str:
    if device == "cuda":
        import ctranslate2

        supported = ctranslate2.get_supported_compute_types("cuda")
        return "int8_float32" if "int8_float32" in supported else "float32"
    return "int8"


def _run(args: argparse.Namespace) -> None:
    from faster_whisper import WhisperModel

    from backend.core.messages import msg
    from backend.core.models_manifest import model_dir, pinned_snapshot_dir

    # A manifest-pinned download writes ``snapshots/<rev>`` but no
    # ``refs/main`` — resolving by name offline on a fresh install then
    # fails. Hand the engine the snapshot dir directly when it is on
    # disk; otherwise keep the legacy name resolution.
    snap = pinned_snapshot_dir(args.model_manifest)
    kw = {
        "device": args.device,
        "compute_type": _compute_type(args.device),
        "download_root": str(model_dir(args.model_manifest)),
    }
    if args.device == "cpu":
        kw["cpu_threads"] = 4
    model = WhisperModel(str(snap) if snap is not None else args.model_id, **kw)
    segments, info = model.transcribe(
        args.wav,
        language=args.language,
        vad_filter=True,
        vad_parameters=dict(_VAD_PARAMETERS),
        word_timestamps=True,
        hallucination_silence_threshold=2.0,
        condition_on_previous_text=False,
        repetition_penalty=1.1,
        no_repeat_ngram_size=3,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        compression_ratio_threshold=2.4,
        multilingual=False,
        language_detection_segments=4,
        append_punctuations=_APPEND_PUNCT,
        prepend_punctuations=_PREPEND_PUNCT,
    )
    text, code, params = msg(
        "language_detected",
        language=info.language or "?",
        prob=round(float(info.language_probability or 0.0), 3),
    )
    _emit({"type": "message", "text": text, "code": code, "params": params})
    out = []
    duration = max(float(args.duration), 1e-6)
    for seg in segments:
        out.append(
            {
                "start": float(seg.start),
                "end": float(seg.end),
                "words": [
                    {
                        "start": float(w.start),
                        "end": float(w.end),
                        "text": str(w.word),
                    }
                    for w in (seg.words or [])
                ],
            }
        )
        _emit(
            {
                "type": "progress",
                "value": 0.1 + 0.9 * min(1.0, float(seg.end) / duration),
            }
        )
    _emit(
        {
            "type": "result",
            "language": info.language,
            "language_prob": float(info.language_probability or 0.0),
            "segments": out,
        }
    )


def main(argv: list[str]) -> None:
    """Never returns — every path ends in ``os._exit`` so no C++
    destructor (the crash source) or atexit handler ever runs."""
    ap = argparse.ArgumentParser(prog="backend.media.transcribe_child")
    ap.add_argument("--wav", required=True)
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--model-manifest", required=True)
    ap.add_argument("--device", choices=["cuda", "cpu"], required=True)
    ap.add_argument("--language", default=None)
    ap.add_argument("--duration", type=float, required=True)
    args = ap.parse_args(argv)

    from backend.core.models_manifest import (
        configure_model_env,
        install_socket_guard,
    )

    configure_model_env()
    if os.environ.get("SOUND_SYSTEM_SOCKET_GUARD") == "1":
        install_socket_guard()
    if args.device == "cuda":
        try:
            from backend.core.gpu import setup_cuda_dlls

            setup_cuda_dlls()
        except Exception:
            pass
    try:
        _run(args)
        code = 0
    except Exception as exc:  # noqa: BLE001 — serialise for the parent
        kind = (
            "oom"
            if isinstance(exc, RuntimeError)
            and "out of memory" in str(exc).lower()
            else "other"
        )
        try:
            _emit(
                {
                    "type": "error",
                    "kind": kind,
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
        except Exception:
            pass
        code = 1
    # No sys.exit: normal interpreter teardown is exactly where the CT2
    # destructor crash lives. os._exit runs nothing — process just ends.
    os._exit(code)


if __name__ == "__main__":
    main(sys.argv[1:])
