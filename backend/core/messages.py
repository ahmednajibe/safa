"""Localizable backend messages (SPEC §11, M5 Task 0).

Every user-facing string the backend emits is keyed by a snake_case code
with raw params — the frontend translates via its dictionaries, the English
template here is the fallback for logs/tests and for codes the frontend
doesn't know yet.

``msg(code, **params)`` renders the English template and returns
``(text, code, params)`` so call sites can fill ``OpResult``/``Progress``/
``err()`` fields in one expression. An unknown code or a missing template
param raises ``KeyError`` — that is a programmer error, and the grep-style
test in ``tests/test_messages.py`` proves it can never ship.
"""
from __future__ import annotations

MESSAGES: dict[str, str] = {
    # op results (SPEC §10)
    "silence_removed": "Removed {seconds} s of silence in {count} places",
    "pauses_shortened": "Shortened {count} pauses, saved {seconds} s",
    "no_speech_found": (
        "No speech found, so nothing was cut. Try Volume threshold mode."
    ),
    "very_quiet_raised": (
        "Very quiet recording — raised as far as safely possible"
    ),
    "noise_reduced": "Cut background noise by about {db} dB",
    "room_resonances_cut": (
        "Reduced room echo: cut resonances at {freqs} Hz"
    ),
    "breaths_reduced": "Made {count} breaths quieter by about {db} dB",
    "separated": "Separated voice and music ({output})",
    "subtitles_written": "Wrote {count} subtitle lines ({language})",
    "language_detected": "Detected language: {language}",
    "trim_outside_preview": (
        "The trim range is outside the previewed part —"
        " nothing was trimmed."
    ),
    # device fallbacks / warnings (SPEC §9)
    "gpu_fallback": (
        "Graphics memory ran out. Running this step on the processor"
        " instead."
    ),
    "nvenc_fallback": (
        "Graphics encoder failed — encoded on the processor instead"
    ),
    "model_downloading": "Downloading {model_id} ({pct} %)",
    "preset_partial": "Some steps were skipped: {skipped_op_ids}",
    # job lifecycle (SPEC §4)
    "job_queued": "Queued — position {position}",
    "job_done": "Done",
    "job_interrupted": "Stopped because the app was closed",
    "job_cancelled": "Cancelled",
    "job_failed": "The job failed",
    "prep_failed": "We couldn't read the audio in this file.",
    # api errors (SPEC §5)
    "insufficient_storage": (
        "Not enough disk space: needs about {needed_gb} GB,"
        " {free_gb} GB free."
    ),
    "unsupported_type": (
        "This file type isn't supported. Try WAV, MP3, MP4…"
    ),
    "unreadable": (
        "We couldn't read this file. It may be damaged or not a media"
        " file."
    ),
    "no_audio": "This video has no audio track. Choose another file.",
    "gpu_unavailable": (
        "The graphics card isn't available on this computer."
    ),
    "too_large": "This file is too large.",
    "media_not_found": "This file was deleted or never existed.",
    "job_not_found": "This job was deleted or never existed.",
    "bad_request": "Invalid request.",
    "bad_param": "Invalid setting: {param}",
    "bad_device": "Invalid device.",
    "bad_output": "Unknown audio format.",
    "bad_preview": "The preview must be between 1 and 60 seconds.",
    "no_such_stream": "This file has no audio track with that number.",
    "not_terminal": "This job is still active.",
    "not_retryable": (
        "Only failed, cancelled or interrupted jobs can be retried."
    ),
    "timeline_not_found": (
        "The timeline isn't available for this job yet."
    ),
    "in_use": "A running job is using this file.",
    "processing": "Still preparing this file.",
    "client_disconnected": "The upload was interrupted.",
    # availability / conflicts (SPEC §2)
    "karaoke_speech_op": "Off while Karaoke is on",
    "planned": "Coming later",
    "not_implemented": "Coming in a later update",
    "module_missing": "Not available on this computer",
    "model_missing": "Model not downloaded yet",
    "unavailable": "Not available on this computer",
}


def msg(code: str, **params) -> tuple[str, str, dict]:
    """``(english_text, code, params)`` for ``code``.

    Raises ``KeyError`` on an unknown code or a missing template param —
    both are programmer errors the message tests are designed to catch.
    """
    template = MESSAGES.get(code)
    if template is None:
        raise KeyError(f"unknown message code: {code}")
    try:
        text = template.format(**params)
    except (KeyError, IndexError) as exc:
        raise KeyError(f"message {code!r}: missing param {exc}") from exc
    return text, code, dict(params)
