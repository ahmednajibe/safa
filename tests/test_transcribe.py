"""M8 Task 1: faster-whisper engine adapter.

- compute type selection (int8_float32 → float32 → cpu int8)
- the silent-input guard (no whisper child spawned, empty result)
- the GPU-failure ladder (gpu child fails → cpu rung, gpu_fallback msg)
- cancel while consuming the child's protocol → child tree killed
- real transcription of the SAPI fixture on CPU int8

The whisper attempt runs in a disposable child process (faster-whisper
1.1.1 + CT2 CUDA teardown abort()s on Windows — upstream #1293), so the
unit tests patch ``_run_child`` / ``subprocess.Popen`` rather than
``WhisperModel``.
"""
from pathlib import Path

import pytest
import soundfile as sf

GENERATED = Path(__file__).parent / "fixtures" / "generated"
SPEECH = GENERATED / "speech.wav"
SILENCE = GENERATED / "silence.wav"


def _silero_ok() -> bool:
    try:
        import silero_vad  # noqa: F401
        import torch  # noqa: F401

        return True
    except Exception:
        return False


def _whisper_ok() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not _silero_ok(), reason="silero-vad/torch missing"),
    pytest.mark.skipif(not _whisper_ok(), reason="faster-whisper missing"),
]


# ---- compute type ---------------------------------------------------------------


def test_compute_type_gpu_prefers_int8_float32(monkeypatch):
    import ctranslate2

    from backend.media import transcribe_child

    monkeypatch.setattr(
        ctranslate2,
        "get_supported_compute_types",
        lambda dev: {"int8_float32", "float32", "int8"},
    )
    assert transcribe_child._compute_type("cuda") == "int8_float32"


def test_compute_type_gpu_falls_back_to_float32(monkeypatch):
    import ctranslate2

    from backend.media import transcribe_child

    monkeypatch.setattr(
        ctranslate2,
        "get_supported_compute_types",
        lambda dev: {"float16", "float32"},
    )
    assert transcribe_child._compute_type("cuda") == "float32"


def test_compute_type_cpu_is_int8():
    from backend.media import transcribe_child

    assert transcribe_child._compute_type("cpu") == "int8"


# ---- silent-input guard ------------------------------------------------------------


def test_silent_input_never_loads_whisper(monkeypatch, tmp_path):
    """silence.wav + language=auto must not spawn the whisper child — the
    library raises ValueError on 0 speech chunks (argon_m8)."""
    from backend.media import transcribe

    def _boom(*a, **kw):
        raise AssertionError("whisper child must not be spawned")

    monkeypatch.setattr(transcribe, "_run_child", _boom)
    res = transcribe.transcribe_file(
        SILENCE, language=None, model="fast", device="cpu",
    )
    assert res.segments == []
    assert res.words == []
    assert res.language is None


def test_empty_wav_never_loads_whisper(monkeypatch, tmp_path):
    from backend.media import transcribe

    empty = tmp_path / "empty.wav"
    sf.write(str(empty), [], 48000, subtype="FLOAT")
    monkeypatch.setattr(
        transcribe,
        "_run_child",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("whisper child must not be spawned")
        ),
    )
    res = transcribe.transcribe_file(
        empty, language="en", model="fast", device="cpu",
    )
    assert res.segments == []


# ---- OOM ladder ---------------------------------------------------------------


def _payload(segments=None, language="en", prob=0.9):
    return {
        "type": "result",
        "language": language,
        "language_prob": prob,
        "segments": segments
        if segments is not None
        else [
            {
                "start": 0.0,
                "end": 1.0,
                "words": [
                    {"start": 0.0, "end": 0.5, "text": " quick"}
                ],
            }
        ],
    }


def test_gpu_child_failure_retries_cpu(monkeypatch):
    """A GPU-rung child failure (OOM classified or hard abort — both are
    just ``ChildTranscribeError`` to the parent) retries on cpu and
    reports ``gpu_fallback`` (SPEC §9 + argon_m8, hardened for #1293)."""
    from backend.media import transcribe

    built = []

    def _flaky(wav, language, model_key, device, duration, report, cancel):
        built.append(device)
        if device == "gpu":
            raise transcribe.ChildTranscribeError(
                "oom", "cublas runtime error: out of memory"
            )
        return _payload()

    monkeypatch.setattr(transcribe, "_run_child", _flaky)
    reports = []
    res = transcribe.transcribe_file(
        SPEECH, language="en", model="fast", device="gpu",
        report=lambda *a: reports.append(a),
    )
    assert built == ["gpu", "cpu"]
    assert res.device == "cpu"
    assert res.oom_fallback is True
    assert any(
        w.text.strip().lower() == "quick" for w in res.words
    )
    codes = [a[2] for a in reports if len(a) >= 4 and a[2]]
    assert "gpu_fallback" in codes
    fb = next(a for a in reports if len(a) >= 4 and a[2] == "gpu_fallback")
    assert fb[3]["op_id"] == "subtitles"


def test_gpu_crash_without_error_line_also_retries(monkeypatch):
    """The abort case: child dies mid-run with no protocol error → kind
    'crash' → same CPU fallback (a teardown abort can't reach the API)."""
    from backend.media import transcribe

    def _crash(wav, language, model_key, device, duration, report, cancel):
        if device == "gpu":
            raise transcribe.ChildTranscribeError(
                "crash", "exit code -1073740791"
            )
        return _payload()

    monkeypatch.setattr(transcribe, "_run_child", _crash)
    res = transcribe.transcribe_file(
        SPEECH, language="en", model="fast", device="gpu",
    )
    assert res.device == "cpu"
    assert res.oom_fallback is True
    assert res.fallback_kind == "crash"


def test_oom_on_cpu_propagates(monkeypatch):
    from backend.media import transcribe

    def _oom(*a, **kw):
        raise transcribe.ChildTranscribeError("oom", "out of memory")

    monkeypatch.setattr(transcribe, "_run_child", _oom)
    with pytest.raises(RuntimeError, match="out of memory"):
        transcribe.transcribe_file(
            SPEECH, language="en", model="fast", device="cpu",
        )


# ---- cancel mid-generator ---------------------------------------------------------


def test_cancel_kills_child_tree(monkeypatch):
    """Cancel lands while consuming the child's protocol lines — the
    parent kills the child process tree and raises ``CancelledError``."""
    import io
    import json

    from backend.media import transcribe
    from backend.pipeline.types import CancelledError

    killed = []

    class _FakePopen:
        def __init__(self, args, **kw):
            self.args = args
            self.returncode = None
            lines = [
                json.dumps({"type": "progress", "value": 0.3}),
                json.dumps({"type": "progress", "value": 0.4}),
            ]
            self.stdout = io.StringIO("\n".join(lines) + "\n")
            self.stderr = io.StringIO("")

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def kill(self):
            killed.append(True)
            self.returncode = -9

    monkeypatch.setattr(
        transcribe.subprocess, "Popen", _FakePopen
    )
    cancel = [False]

    def report(*a):
        if a and isinstance(a[0], (int, float)) and a[0] > 0.15:
            cancel[0] = True

    consumed = []
    try:
        transcribe.transcribe_file(
            SPEECH, language="en", model="fast", device="cpu",
            report=report, should_cancel=lambda: cancel[0],
        )
    except CancelledError as exc:
        consumed.append(exc)
    assert consumed and isinstance(consumed[0], CancelledError)
    assert killed == [True]


def test_child_error_line_becomes_child_transcribe_error(monkeypatch):
    """A protocol ``error`` line maps to ``ChildTranscribeError`` with the
    child's classified kind (oom vs other)."""
    import io
    import json

    from backend.media import transcribe

    class _ErrPopen:
        def __init__(self, args, **kw):
            self.returncode = 1
            self.stdout = io.StringIO(
                json.dumps(
                    {
                        "type": "error",
                        "kind": "oom",
                        "detail": "RuntimeError: cublas out of memory",
                    }
                )
                + "\n"
            )
            self.stderr = io.StringIO("")

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return 1

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(transcribe.subprocess, "Popen", _ErrPopen)
    with pytest.raises(transcribe.ChildTranscribeError) as ei:
        transcribe._run_child(
            SPEECH, "en", "fast", "cpu", 5.0, None, None
        )
    assert ei.value.kind == "oom"


# ---- real transcription (CPU int8) -----------------------------------------------


def test_transcribe_sapi_fixture_cpu():
    """The SAPI sentence must come back recognisably (Review Focus 3)."""
    from backend.media import transcribe
    from backend.core.capabilities import _model_present

    if not _model_present("whisper_small"):
        pytest.skip("whisper_small not cached")
    res = transcribe.transcribe_file(
        SPEECH, language=None, model="fast", device="cpu",
    )
    text = " ".join(w.text for w in res.words).lower()
    for word in ("quick", "brown", "fox"):
        assert word in text, text
    assert res.language == "en"
    assert res.language_prob > 0.5
    assert res.device == "cpu"
    # word timestamps present and ordered
    for w in res.words:
        assert w.end >= w.start
    starts = [w.start for w in res.words]
    assert starts == sorted(starts)
    assert res.segments  # grouped utterances exist


def test_transcribe_reports_language_and_progress():
    from backend.core.capabilities import _model_present
    from backend.media import transcribe

    if not _model_present("whisper_small"):
        pytest.skip("whisper_small not cached")
    events = []
    res = transcribe.transcribe_file(
        SPEECH, language="en", model="fast", device="cpu",
        report=lambda *a: events.append(a),
    )
    assert res.language == "en"
    codes = [a[2] for a in events if len(a) >= 4 and a[2]]
    assert "language_detected" in codes
    ld = next(
        a for a in events
        if len(a) >= 4 and a[2] == "language_detected"
    )
    assert ld[3]["language"] == "en"
    assert 0.0 <= ld[3]["prob"] <= 1.0
    # monotonically rising stage pct (0..1)
    pcts = [a[0] for a in events if isinstance(a[0], (int, float))]
    assert pcts and pcts[-1] == pytest.approx(1.0)
    assert pcts == sorted(pcts)
