"""faster-whisper engine adapter (M8 Task 1, SPEC §2/§9, argon_m8).

faster-whisper 1.1.1 + CTranslate2 on Windows aborts the whole process
when a CUDA WhisperModel is torn down — ``del model`` or interpreter exit
lands in the ``cuda_malloc_async`` teardown and calls ``abort()``
(SYSTRAN/faster-whisper#1293). That is not a Python exception, so the
whisper attempt runs in a *disposable child process*
(``backend.media.transcribe_child``) which writes a JSON-lines protocol to
stdout and exits via ``os._exit`` — the destructor never runs and any
crash stays confined to the child.

- Model keys ``fast`` → ``small`` (Systran repo) and ``accurate`` →
  ``large-v3-turbo`` (mobiuslabsgmbh repo); weights cache under
  ``models/whisper`` via the manifest's ``download_root``.
- Compute: GPU ``int8_float32`` when CT2 supports it else ``float32``;
  CPU ``int8`` with ``cpu_threads=4`` (inside the child).
- ``transcribe`` runs with the argon_m8 parameter set — word timestamps,
  the 500 ms VAD silencer, hallucination thresholds, no previous-text
  conditioning, and the Arabic-extended punctuation merge lists so
  ``، ؛ ؟ »`` attach to their word instead of becoming orphan tokens.
- Silent-input guard: Silero reports no speech (or the file is digital
  silence) → return an empty transcript *without* loading whisper — the
  library's ``detect_language`` raises ``ValueError`` on 0 speech chunks
  when language is auto.
- GPU failure ladder: any child failure on the GPU rung (OOM error line,
  dead process, protocol abort) falls back to the CPU rung and reports
  ``gpu_fallback`` — a hard CT2 abort can never reach the caller, so
  "OOM RuntimeError → CPU retry" generalises to "GPU crash → CPU retry".
- ``should_cancel()`` is polled between protocol lines; on cancel the
  child tree is killed and ``CancelledError`` is raised.

Heavy imports (faster_whisper, ctranslate2, torch) live in the child —
this module itself only needs subprocess/psutil, so even a *worker*
stays light until a real transcription runs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from backend.core.messages import msg
from backend.core.paths import ROOT
from backend.pipeline.types import CancelledError, JobDevice

Device = Literal["gpu", "cpu"]

# registry param ``model`` → faster-whisper model size name → manifest entry
MODEL_IDS = {"fast": "small", "accurate": "large-v3-turbo"}
_MANIFEST_KEYS = {"fast": "whisper_small", "accurate": "whisper_turbo"}

ReportFn = Callable[..., None] | None
CancelFn = Callable[[], bool] | None

if sys.platform == "win32":
    _CREATEFLAGS = (
        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    )
else:
    _CREATEFLAGS = 0


@dataclass
class SubWord:
    start: float
    end: float
    text: str


@dataclass
class SubSegment:
    start: float
    end: float
    words: list[SubWord] = field(default_factory=list)


@dataclass
class Transcript:
    segments: list[SubSegment]
    language: str | None
    language_prob: float
    device: JobDevice
    # True when a GPU attempt failed and the result came from the CPU
    # rung; ``fallback_kind`` carries the child's classified reason
    # ("oom" / "crash" / "other") — a teardown abort counts as "crash".
    oom_fallback: bool = False
    fallback_kind: str | None = None

    @property
    def words(self) -> list[SubWord]:
        return [w for s in self.segments for w in s.words]


class ChildTranscribeError(RuntimeError):
    """The whisper child failed — carries the protocol error kind.

    ``kind`` is ``"oom"`` when the child classified a CT2 RuntimeError
    containing "out of memory", ``"crash"`` when it died without an error
    line (the teardown abort or a genuine mid-run crash), and ``"other"``
    for everything else.
    """

    def __init__(self, kind: str, detail: str):
        super().__init__(f"whisper subprocess failed ({kind}): {detail}")
        self.kind = kind
        self.detail = detail


def _cancel(should_cancel: CancelFn) -> None:
    if should_cancel and should_cancel():
        raise CancelledError()


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` plus descendants (same pattern as the supervisor)."""
    try:
        import psutil

        children = psutil.Process(proc.pid).children(recursive=True)
    except Exception:
        children = []
    try:
        proc.kill()
    except Exception:
        pass
    for child in children:
        try:
            child.kill()
        except Exception:
            pass


def _drain(stream, bucket: list) -> None:
    """stderr → bounded tail buffer; without it the child blocks once the
    pipe fills (tqdm download bars easily exceed the Windows pipe)."""
    try:
        for chunk in iter(stream.readline, ""):
            bucket.append(chunk)
            if sum(len(c) for c in bucket) > 65536:
                del bucket[: len(bucket) // 2]
    except Exception:
        pass


def _model_cached(model_key: str) -> bool:
    try:
        from backend.core.capabilities import _model_present

        return _model_present(_MANIFEST_KEYS[model_key])
    except Exception:
        return False


def _run_child(
    wav: Path,
    language: str | None,
    model_key: str,
    device: Device,
    duration: float,
    report: ReportFn,
    should_cancel: CancelFn,
) -> dict:
    """Spawn ``transcribe_child`` and consume its JSON-lines protocol.

    Returns the ``result`` payload. Raises ``ChildTranscribeError`` for a
    reported error or an unreported process death; ``CancelledError``
    after killing the child tree.
    """
    emit = report or (lambda *a: None)
    args = [
        sys.executable,
        "-m",
        "backend.media.transcribe_child",
        "--wav",
        str(wav),
        "--model-id",
        MODEL_IDS[model_key],
        "--model-manifest",
        _MANIFEST_KEYS[model_key],
        "--device",
        "cuda" if device == "gpu" else "cpu",
        "--duration",
        repr(float(duration)),
    ]
    if language:
        args += ["--language", language]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if device == "cpu":
        env["CUDA_VISIBLE_DEVICES"] = ""  # hard CPU ceiling for the retry
    proc = subprocess.Popen(
        args,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATEFLAGS,
    )
    stderr_tail: list[str] = []
    drain = threading.Thread(
        target=_drain, args=(proc.stderr, stderr_tail), daemon=True
    )
    drain.start()
    result: dict | None = None
    error: dict | None = None
    try:
        for line in proc.stdout:
            _cancel(should_cancel)
            try:
                evt = json.loads(line)
            except (ValueError, TypeError):
                continue  # stray non-protocol output is ignored
            kind = evt.get("type")
            if kind == "progress":
                v = evt.get("value")
                if isinstance(v, (int, float)):
                    emit(float(v))
            elif kind == "message":
                emit(
                    None,
                    evt.get("text") or "",
                    evt.get("code"),
                    evt.get("params") or {},
                )
            elif kind == "result":
                result = evt
            elif kind == "error":
                error = evt
    except BaseException:
        # Cancel or a reader-side error: kill the child before reaping so
        # a still-transcribing process doesn't stall us.
        if proc.poll() is None:
            _kill_tree(proc)
        raise
    finally:
        if proc.poll() is None:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
        drain.join(timeout=5)
    if error is not None:
        raise ChildTranscribeError(
            error.get("kind") or "other", error.get("detail") or ""
        )
    if result is None:
        tail = "".join(stderr_tail).strip()[-400:]
        raise ChildTranscribeError(
            "crash",
            f"exit code {proc.returncode}"
            + (f"; stderr tail: {tail}" if tail else ""),
        )
    return result


def _attempt(
    wav: Path,
    language: str | None,
    model_key: str,
    device: Device,
    duration: float,
    report: ReportFn,
    should_cancel: CancelFn,
) -> Transcript:
    emit = report or (lambda *a: None)
    if not _model_cached(model_key):
        emit(
            None,
            *msg("model_downloading", model_id=MODEL_IDS[model_key], pct=0),
        )
    payload = _run_child(
        wav, language, model_key, device, duration, report, should_cancel
    )
    out = [
        SubSegment(
            float(s["start"]),
            float(s["end"]),
            [
                SubWord(float(w["start"]), float(w["end"]), str(w["text"]))
                for w in s.get("words") or []
            ],
        )
        for s in payload.get("segments") or []
    ]
    return Transcript(
        segments=out,
        language=payload.get("language"),
        language_prob=float(payload.get("language_prob") or 0.0),
        device=device,
    )


def transcribe_file(
    wav: Path,
    language: str | None = None,
    model: str = "fast",
    device: Device = "gpu",
    report: ReportFn = None,
    should_cancel: CancelFn = None,
) -> Transcript:
    """Transcribe ``wav`` (48 kHz float WAV) into word-timed segments.

    ``language=None`` = auto-detect. Returns the empty transcript on
    speech-free input (the whisper child is never spawned) and retries the
    whole file once on CPU when the GPU attempt fails — the child's JSON
    protocol means even a C++ ``abort()`` degrades to a clean fallback.
    """
    if model not in MODEL_IDS:
        raise ValueError(f"unknown whisper model key: {model}")
    emit = report or (lambda *a: None)
    _cancel(should_cancel)
    info_duration = None
    try:
        import soundfile as sf

        info_duration = sf.info(str(wav)).frames / max(
            sf.info(str(wav)).samplerate, 1
        )
    except Exception:
        pass
    duration = float(info_duration or 0.0)

    # Silent-input guard: no speech → valid empty transcript, no whisper
    # load (argon_m8: detect_language raises on 0 speech chunks in auto).
    from backend.media.silence_detect import speech_intervals_vad

    speech = speech_intervals_vad(
        wav,
        report=lambda p: emit(0.1 * p),
        should_cancel=should_cancel,
    )
    if not speech or duration <= 0.0:
        emit(1.0)
        return Transcript(
            segments=[], language=None, language_prob=0.0, device=device
        )

    devices: list[Device] = ["gpu", "cpu"] if device == "gpu" else ["cpu"]
    fell_back = False
    fallback_kind: str | None = None
    exc0: BaseException | None = None
    for i, dev in enumerate(devices):
        if i > 0:
            emit(
                None,
                *msg("gpu_fallback", op_id="subtitles"),
            )
        try:
            res = _attempt(
                wav, language, model, dev, duration,
                report, should_cancel,
            )
            res.oom_fallback = fell_back
            res.fallback_kind = fallback_kind
            emit(1.0)
            return res
        except BaseException as exc:
            if dev == "cpu" or not isinstance(exc, ChildTranscribeError):
                raise
            fell_back = True
            fallback_kind = exc.kind
            exc0 = exc
    raise exc0  # unreachable on this ladder; defensive
