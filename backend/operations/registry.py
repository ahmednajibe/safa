"""Operation registry (SPEC §2, UI_DESIGN §7): pure data, no processing.

Every op declares order, params schema, device needs and what it requires to
be selectable. Availability is computed from capabilities elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ParamType = Literal["number", "select", "bool"]
Device = Literal["cpu", "gpu", "gpu-optional"]


@dataclass
class ParamSpec:
    key: str
    label: str
    type: ParamType
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    unit: str | None = None
    options: list[dict] | None = None  # items: {"value", "label"}
    advanced: bool = False
    help: str | None = None


@dataclass
class OpSpec:
    id: str
    order: int
    name: str
    description: str
    engine: str
    device: Device
    params: list[ParamSpec] = field(default_factory=list)
    changes_timeline: bool = False
    requires_modules: list[str] = field(default_factory=list)
    requires_models: list[str] = field(default_factory=list)
    planned: bool = False


def _opts(*pairs: tuple[Any, str]) -> list[dict]:
    return [{"value": v, "label": lbl} for v, lbl in pairs]


OPERATIONS: dict[str, OpSpec] = {
    "trim": OpSpec(
        id="trim", order=10,
        name="Trim ends",
        description="Keep only the part between a start and end time.",
        engine="ffmpeg", device="cpu", changes_timeline=True,
        params=[
            ParamSpec("start_s", "Start at", "number", 0, min=0, unit="s"),
            ParamSpec("end_s", "End at", "number", None, min=0, unit="s",
                      help="Leave empty to keep until the end"),
        ],
    ),
    "channel": OpSpec(
        id="channel", order=15,
        name="Fix channels",
        description="Keep stereo, combine to mono, or use only the left or right side.",
        engine="ffmpeg", device="cpu",
        params=[
            ParamSpec("mode", "Channels", "select", "keep", options=_opts(
                ("keep", "Keep stereo"), ("mono", "Combine to mono"),
                ("left", "Left only"), ("right", "Right only"),
            )),
        ],
    ),
    "highpass": OpSpec(
        id="highpass", order=20,
        name="Remove low rumble",
        description="Cuts deep thumps, desk bumps and air-conditioner rumble.",
        engine="pedalboard / ffmpeg", device="cpu",
        params=[
            ParamSpec("cutoff_hz", "Cutoff", "number", 90,
                      min=40, max=200, unit="Hz"),
        ],
    ),
    "dehum": OpSpec(
        id="dehum", order=25,
        name="Remove electrical hum",
        description="Removes steady 50/60 Hz buzz from power lines and equipment.",
        engine="ffmpeg", device="cpu",
        params=[
            ParamSpec("base", "Hum frequency", "select", 50, options=_opts(
                (50, "50 Hz"), (60, "60 Hz"),
            )),
            ParamSpec("harmonics", "Harmonics", "number", 4,
                      min=1, max=8, advanced=True),
        ],
    ),
    "separate": OpSpec(
        id="separate", order=30,
        name="Separate voice and music",
        description="Keep the voice, keep the music, or export both.",
        engine="audio-separator", device="gpu-optional",
        requires_modules=["audio_separator"],
        requires_models=["htdemucs", "kim_vocal_2"],
        params=[
            ParamSpec("output", "Keep", "select", "vocals", options=_opts(
                ("vocals", "Keep voice"),
                ("instrumental", "Keep music (karaoke)"),
                ("both", "Export both"),
            )),
            ParamSpec("model", "Model", "select", "htdemucs", options=_opts(
                ("htdemucs", "Balanced"),
                ("kim_vocal_2", "Tighter music removal"),
            ), advanced=True),
        ],
    ),
    "dereverb": OpSpec(
        id="dereverb", order=42,
        name="Reduce room echo",
        description="Dries up the hollow sound of big or bare rooms.",
        engine="wpe + scipy/pedalboard", device="cpu",
        requires_modules=["pedalboard"],
        params=[
            ParamSpec("strength", "Strength", "select", "normal",
                      options=_opts(
                          ("gentle", "Gentle"), ("normal", "Normal"),
                          ("strong", "Strong"),
                      )),
            ParamSpec("wpe", "Dereverb (WPE)", "select", "on",
                      options=_opts(
                          ("off", "Off"), ("on", "Light"),
                          ("strong", "Strong"),
                      ), advanced=True),
        ],
    ),
    "breaths": OpSpec(
        id="breaths", order=46,
        name="Reduce breaths",
        description="Makes breathing between sentences quieter without"
                    " cutting it out.",
        engine="numpy/scipy + silero-vad", device="cpu",
        requires_modules=["silero_vad"], requires_models=["silero_vad"],
        params=[
            ParamSpec("reduce_db", "How much quieter", "number", 12,
                      min=6, max=20, unit="dB"),
            ParamSpec("sensitivity", "Sensitivity", "select", "normal",
                      options=_opts(
                          ("gentle", "Gentle"), ("normal", "Normal"),
                          ("strong", "Strong"),
                      )),
        ],
    ),
    "denoise": OpSpec(
        id="denoise", order=40,
        name="Reduce background noise",
        description="Lowers hiss, fans, traffic and room noise while keeping speech natural.",
        engine="deepfilternet3 / ffmpeg", device="gpu-optional",
        requires_modules=["df"], requires_models=["deepfilternet3"],
        params=[
            ParamSpec("engine", "Engine", "select", "ai", options=_opts(
                ("ai", "AI (DeepFilterNet)"), ("classic", "Classic"),
            )),
            ParamSpec("strength_db", "Strength", "number", 24,
                      min=6, max=40, unit="dB"),
        ],
    ),
    "declick": OpSpec(
        id="declick", order=38,
        name="Remove mouth clicks",
        description="Removes saliva clicks, lip smacks and crackle without touching consonants.",
        engine="numpy/scipy + ffmpeg", device="cpu",
        params=[
            ParamSpec("mode", "Click type", "select", "mouth",
                      options=_opts(
                          ("mouth", "Mouth clicks and saliva"),
                          ("crackle", "Digital crackle"),
                      )),
            ParamSpec("sensitivity", "Sensitivity", "select", "normal",
                      options=_opts(
                          ("gentle", "Gentle"), ("normal", "Normal"),
                          ("strong", "Strong"),
                      )),
        ],
    ),
    "enhance": OpSpec(
        id="enhance", order=50,
        name="Polish voice",
        description="Balances tone, softens harsh \"s\" sounds and evens out loud and quiet words.",
        engine="pedalboard + ffmpeg", device="cpu",
        requires_modules=["pedalboard"],
        params=[
            ParamSpec("preset", "Preset", "select", "voice", options=_opts(
                ("voice", "Voice"), ("bright", "Bright"), ("warm", "Warm"),
            )),
            ParamSpec("reduce_boom", "Reduce boominess", "bool", True),
            ParamSpec("deess", "Soften \"s\" sounds", "bool", True),
        ],
    ),
    "enhance_ai": OpSpec(
        id="enhance_ai", order=55,
        name="Restore damaged voice",
        description="Tries to rebuild muffled or badly recorded speech (experimental).",
        engine="resemble-enhance", device="gpu-optional", planned=True,
    ),
    "silence": OpSpec(
        id="silence", order=60,
        name="Cut silence",
        description="Removes the silent parts so only the speech is left"
                    " (or just shortens long pauses).",
        engine="silero-vad / ffmpeg", device="cpu", changes_timeline=True,
        requires_modules=["silero_vad"], requires_models=["silero_vad"],
        params=[
            ParamSpec("action", "Action", "select", "remove", options=_opts(
                ("remove", "Remove silence"),
                ("shorten", "Shorten pauses"),
            )),
            ParamSpec("min_silence_s", "Shortest silence to cut", "number",
                      0.3, min=0.1, max=3.0, step=0.05, unit="s"),
            ParamSpec("keep_pause_s", "Pause length after shortening",
                      "number", 0.25, min=0, max=2, step=0.05, unit="s"),
            ParamSpec("mode", "Detection", "select", "vad", options=_opts(
                ("vad", "Speech detection"), ("amplitude", "Volume threshold"),
            )),
            ParamSpec("pad_s", "Keep around speech", "number", 0.1,
                      min=0, max=0.5, step=0.05, unit="s", advanced=True),
            ParamSpec("threshold_db", "Silence threshold", "number", -40,
                      min=-80, max=-20, unit="dB", advanced=True),
        ],
    ),
    "speed": OpSpec(
        id="speed", order=70,
        name="Change speed",
        description="Faster or slower without the chipmunk effect.",
        engine="ffmpeg", device="cpu", changes_timeline=True,
        params=[
            ParamSpec("factor", "Speed", "number", 1.0,
                      min=0.5, max=2.0, step=0.05, unit="×"),
        ],
    ),
    "loudness": OpSpec(
        id="loudness", order=80,
        name="Even out volume",
        description="Sets a steady listening level (Podcast −16 / YouTube −14) and stops clipping.",
        engine="ffmpeg", device="cpu",
        params=[
            ParamSpec("target", "Target", "select", "podcast", options=_opts(
                ("podcast", "Podcast (−16 LUFS)"),
                ("youtube", "YouTube (−14 LUFS)"),
                ("custom", "Custom"),
            )),
            ParamSpec("lufs", "Level", "number", -16,
                      min=-30, max=-6, unit="LUFS", advanced=True),
            ParamSpec("true_peak_db", "True peak", "number", -1,
                      min=-3, max=0, unit="dBTP", advanced=True),
        ],
    ),
    "subtitles": OpSpec(
        id="subtitles", order=90,
        name="Create subtitles",
        description="Writes timed captions (SRT/VTT) from the final audio.",
        engine="faster-whisper", device="gpu-optional",
        requires_modules=["faster_whisper"], requires_models=["whisper_small"],
        params=[
            ParamSpec("language", "Language", "select", "auto", options=_opts(
                ("auto", "Auto-detect"), ("en", "English"), ("ar", "Arabic"),
                ("fr", "French"), ("de", "German"), ("es", "Spanish"),
                ("tr", "Turkish"),
            )),
            ParamSpec("format", "Format", "select", "srt", options=_opts(
                ("srt", "SRT"), ("vtt", "VTT"), ("both", "Both"),
            )),
        ],
    ),
}


def ops_in_order() -> list[OpSpec]:
    return sorted(OPERATIONS.values(), key=lambda op: op.order)


PRESETS: dict[str, dict] = {
    "podcast": {
        "name": "Podcast cleanup",
        "ops": {
            "highpass": {},
            "dehum": {},
            "declick": {"mode": "mouth"},
            "denoise": {"engine": "ai", "strength_db": 24},
            "dereverb": {"strength": "gentle"},
            "breaths": {},
            "enhance": {},
            "silence": {"action": "remove"},
            "loudness": {"target": "podcast"},
        },
    },
    "remove_music": {
        "name": "Remove background music",
        "ops": {
            "separate": {"output": "vocals"},
            "denoise": {},
            "loudness": {},
        },
    },
    "echoey_room": {
        "name": "Fix echoey room",
        "ops": {
            "denoise": {"strength_db": 24},
            "dereverb": {"strength": "strong"},
            "enhance": {},
            "loudness": {},
        },
    },
    "lecture": {
        "name": "Lecture",
        "ops": {
            "highpass": {},
            "dehum": {},
            "declick": {"mode": "mouth"},
            "denoise": {"engine": "ai", "strength_db": 24},
            "dereverb": {"strength": "gentle"},
            "breaths": {},
            "enhance": {},
            "silence": {"action": "remove"},
            "loudness": {},
            "subtitles": {},
        },
    },
    "karaoke": {
        "name": "Karaoke",
        "ops": {
            "separate": {"output": "instrumental"},
            "loudness": {"target": "youtube"},
        },
    },
    "jump_cut": {
        "name": "Video jump-cut",
        "ops": {
            "declick": {"mode": "mouth"},
            "denoise": {"engine": "ai", "strength_db": 24},
            "dereverb": {"strength": "gentle"},
            "breaths": {},
            "enhance": {},
            "silence": {"action": "remove"},
            "loudness": {"target": "youtube"},
        },
    },
}
