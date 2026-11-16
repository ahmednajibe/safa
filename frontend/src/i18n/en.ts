/** English dictionary (UI_DESIGN §7, §12) — the typed shape `ar.ts` must
 * satisfy. Backend English strings stay the fallback for unknown codes. */

export type PluralForms = {
  count: string; // which param key holds the number
  forms: {
    zero?: string;
    one?: string;
    two?: string;
    few?: string;
    many?: string;
    other: string;
  };
};

export type MsgVal =
  | string
  | {
      text: string;
      /** Whole-message template used instead of `text` when a plural's
       *  count param is 0 ("No breaths found"). */
      zero?: string;
      plurals?: Record<string, PluralForms>;
    };

export interface OpText {
  name: string;
  desc: string;
  params: Record<string, string>;
  help: Record<string, string>;
  options: Record<string, Record<string, string>>;
}

const op = (
  name: string,
  desc: string,
  params: Record<string, string> = {},
  options: Record<string, Record<string, string>> = {},
  help: Record<string, string> = {},
): OpText => ({ name, desc, params, help, options });

export const en = {
  lang: { button: "العربية" },
  appName: "Sound System",
  topBar: {
    history: "History",
    lang: "العربية",
    settings: "Settings",
    shortcuts: "Keyboard shortcuts",
  },
  drop: {
    title: "Drop audio or video here",
    choose: "Choose file",
    meta: "WAV, MP3, FLAC, M4A, MP4, MKV, MOV · stays on this computer",
    release: "Release to add",
    uploading: "Uploading",
    reading: "Reading file…",
    preparing: "Preparing file…",
    remove: "Remove this file",
  },
  err: {
    noAudio: "This video has no audio track. Choose another file.",
    unsupported: "This file type isn't supported.",
    uploadFailed: "The upload failed. Try again.",
    serverUnreachable: "Server not reachable",
  },
  inspector: {
    replace: "Replace file",
    track: "Audio track",
    trackN: "Track {n}",
    notMeasured: "Not measured",
    stereo: "stereo",
    mono: "mono",
    channelsN: "{n} channels",
    khz: "{n} kHz",
    lufs: "{n} LUFS",
    videoTag: "{w}×{h}",
  },
  rack: {
    header: "Steps run top to bottom",
    needFile: "Add a file to start",
    nOn: "{on} of {total} on",
    advanced: "Advanced",
    reset: "Reset",
    steps: "Steps",
    editSteps: "Edit",
  },
  preset: {
    podcast: "Podcast cleanup",
    remove_music: "Remove background music",
    echoey_room: "Fix echoey room",
    lecture: "Lecture",
    karaoke: "Karaoke",
    jump_cut: "Video jump-cut",
    custom: "Custom",
    turnOff: "Turn all off",
    partial: "{name} isn't available on this computer; the rest was applied.",
  },
  pill: {
    comingLater: "Coming later",
    modelMissing: "Model not downloaded yet — connect to the internet once",
    unavailable: "Not available on this computer",
    notImplemented: "Coming in a later update",
    gpu: "GPU",
    cpu: "Processor",
    preview: "Preview",
    outOfDate: "Preview out of date",
    saved: "Saved",
  },
  models: {
    download: "Download model (~{mb} MB)",
    downloading: "Downloading… {pct}%",
    downloadingWait: "Downloading…",
    cancel: "Cancel",
    retry: "Try again",
    failed: "Download failed — check your internet and try again.",
    done: "Model downloaded",
    ariaDownloading: "Downloading model, {pct} percent",
  },
  device: {
    label: "Process on",
    gpu: "Graphics card",
    cpu: "Processor",
    gpuReady: "GPU ready",
    cpuOnly: "CPU only",
    fallback: "Graphics memory ran out. Running this step on the processor instead.",
  },
  action: {
    process: "Process",
    preview: "Preview region",
    previewShort: "Preview",
    cancel: "Cancel",
    retry: "Retry",
    download: "Download",
    close: "Close",
    delete: "Delete",
    keep: "Keep",
    save: "Save",
  },
  output: {
    format: "Output",
    keepVideo: "Keep video",
    formats: { wav: "WAV", flac: "FLAC", mp3: "MP3", aac: "AAC" },
  },
  ab: {
    original: "Original",
    processed: "Processed",
    jumped: "Jumped to {t} — this part was removed",
    compareHint: "A / B to compare",
  },
  preview: {
    start: "Start",
    end: "End",
    unit: "s",
    hint: "Try a short part first",
    running: "Making preview…",
    length: "The preview must be between 1 and 60 seconds.",
    listenRegion: "Play region",
  },
  results: { title: "Results" },
  player: {
    play: "Play",
    pause: "Pause",
  },
  well: {
    waveform: "Waveform",
    loading: "Drawing the waveform…",
    zoomIn: "Zoom in on the waveform",
    zoomOut: "Zoom out on the waveform",
    region: "Preview region",
    regionStart: "Preview start",
    regionEnd: "Preview end",
    video: "Video preview",
  },
  history: {
    title: "History",
    empty: "Your processed recordings will appear here.",
    keptUntil: "Kept until {date}",
    today: "Today",
    yesterday: "Yesterday",
    saveAgain: "Save again",
    confirmDelete: "Delete this job and its files?",
    deleted: "Deleted",
    previewTag: "Preview",
    kept: "Kept",
    keepForever: "Keep forever",
    state: {
      completed: "Done",
      failed: "Failed",
      cancelled: "Cancelled",
      interrupted: "Stopped because the app was closed",
      queued: "Queued",
      running: "Running",
    },
  },
  settings: {
    title: "Save results",
    ask: "Ask every time",
    auto: "Save automatically to",
    change: "Change…",
    typePath: "Type a path instead",
    picking: "Choose the folder in the window that opened…",
    showInFolder: "Show in folder",
    invalid: "This folder can't be used. Choose another one.",
    pickerUnavailable: "The folder window couldn't open on this computer.",
    pickerBusy: "A folder window is already open.",
    pathPlaceholder: "D:\\Recordings",
    savePath: "Save path",
    notSet: "No folder chosen",
    keepTitle: "Keep processed results for",
    keep1: "1 day",
    keep7: "7 days",
    keep30: "30 days",
    keepAlways: "Always",
    diagnosticsTitle: "Help us fix problems",
    diagnosticsCopy: "Copy diagnostics",
    diagnosticsCopied: "Copied — paste it into your message to support.",
    diagnosticsFailed: "Couldn't copy. Try again.",
  },
  save: {
    as: "Save {kind}…",
    copy: "Save a copy…",
    all: "Save all to folder…",
    saving: "Saving {pct}%…",
    savingAll: "Saving {n} of {total}: {name}…",
    saved: "Saved",
    savedTo: "Saved to {folder}",
    autoFailed: "Couldn't save to {folder}. Save it manually.",
    error: "Couldn't save the file. Try again.",
    warn: "Warning",
  },
  fileKind: {
    video: "MP4",
    audio: "audio",
    subtitle: "subtitles",
    stem: "stem",
    file: "file",
  },
  progress: {
    step: "Step {n} of {total}",
    eta: "About {t} left",
    etaShort: "Under a minute left",
    estimating: "Estimating…",
    working: "Working…",
    queued: "Waiting for the current job to finish",
    downloading: "Downloading voice model (first time only) · {pct}%",
    done: "Done",
    cancelled: "Cancelled",
    failed: "The job failed",
    previewDone: "Preview ready — press B to compare",
  },
  estimate: {
    gpuSub: "Uses {gpu} · {bucket} · switches to the processor if graphics memory runs out",
    cpuSub: "Uses the processor · {bucket} · graphics card stays free",
    bucket: {
      under_1m: "under a minute",
      about_n_m: "about {minutes} min",
      over_30m: "over 30 min",
    },
  },
  cutList: {
    title: "Removed parts",
    removed: "Removed {t}",
    empty: "Nothing was removed.",
  },
  shortcuts: {
    title: "Shortcuts",
    play: "Play / pause",
    original: "Listen to the original",
    processed: "Listen to the processed audio",
    seek: "Seek 5 seconds",
    preview: "Play the preview region",
    nudge: "Move the preview region",
    close: "Close drawer or popover",
  },
  misc: {
    noSteps: "Turn on at least one step",
    waitingFile: "Waiting for the file to finish preparing…",
  },
  stage: {
    decode: "Reading file",
    encode: "Saving audio",
    video: "Rebuilding video",
    done: "Done",
  },
  conflict: {
    karaoke_speech_op: {
      declick: "Off while Karaoke is on: click removal works on speech.",
      denoise: "Off while Karaoke is on: noise reduction works on speech.",
      dereverb: "Off while Karaoke is on: echo removal is tuned for speech.",
      breaths: "Off while Karaoke is on: breath detection needs a voice.",
      enhance: "Off while Karaoke is on: voice polish works on speech.",
      silence: "Off while Karaoke is on: speech detection needs a voice.",
      subtitles: "Off while Karaoke is on: transcription needs a voice.",
      other: "Off while Karaoke is on.",
    },
  },
  op: {
    trim: op(
      "Trim ends",
      "Keep only the part between a start and end time.",
      { start_s: "Start at", end_s: "End at" },
      {},
      { end_s: "Leave empty to keep until the end" },
    ),
    channel: op(
      "Fix channels",
      "Keep stereo, combine to mono, or use only the left or right side.",
      { mode: "Channels" },
      {
        mode: {
          keep: "Keep stereo",
          mono: "Combine to mono",
          left: "Left only",
          right: "Right only",
        },
      },
    ),
    highpass: op(
      "Remove low rumble",
      "Cuts deep thumps, desk bumps and air-conditioner rumble.",
      { cutoff_hz: "Cutoff" },
    ),
    dehum: op(
      "Remove electrical hum",
      "Removes steady 50/60 Hz buzz from power lines and equipment.",
      { base: "Hum frequency", harmonics: "Harmonics" },
      { base: { "50": "50 Hz", "60": "60 Hz" } },
    ),
    separate: op(
      "Separate voice and music",
      "Keep the voice, keep the music, or export both.",
      { output: "Keep", model: "Model" },
      {
        output: {
          vocals: "Keep voice",
          instrumental: "Keep music (karaoke)",
          both: "Export both",
        },
        model: {
          htdemucs: "Balanced",
          kim_vocal_2: "Tighter music removal",
        },
      },
    ),
    dereverb: op(
      "Reduce room echo",
      "Dries up the hollow sound of big or bare rooms.",
      { strength: "Strength", wpe: "Dereverb (WPE)" },
      {
        strength: { gentle: "Gentle", normal: "Normal", strong: "Strong" },
        wpe: { off: "Off", on: "Light", strong: "Strong" },
      },
    ),
    breaths: op(
      "Reduce breaths",
      "Makes breathing between sentences quieter without cutting it out.",
      { reduce_db: "How much quieter", sensitivity: "Sensitivity" },
      {
        sensitivity: { gentle: "Gentle", normal: "Normal", strong: "Strong" },
      },
    ),
    denoise: op(
      "Reduce background noise",
      "Lowers hiss, fans, traffic and room noise while keeping speech natural.",
      { engine: "Engine", strength_db: "Strength" },
      {
        engine: {
          ai: "AI (DeepFilterNet)",
          classic: "Classic",
        },
      },
    ),
    declick: op(
      "Remove mouth clicks",
      "Removes saliva clicks, lip smacks and crackle without touching consonants.",
      { mode: "Click type", sensitivity: "Sensitivity" },
      {
        mode: {
          mouth: "Mouth clicks and saliva",
          crackle: "Digital crackle",
        },
        sensitivity: { gentle: "Gentle", normal: "Normal", strong: "Strong" },
      },
    ),
    enhance: op(
      "Polish voice",
      "Balances tone, softens harsh \"s\" sounds and evens out loud and quiet words.",
      { preset: "Preset", reduce_boom: "Reduce boominess", deess: "Soften \"s\" sounds" },
      {
        preset: { voice: "Voice", bright: "Bright", warm: "Warm" },
      },
    ),
    enhance_ai: op(
      "Restore damaged voice",
      "Tries to rebuild muffled or badly recorded speech (experimental).",
    ),
    declick_final: op(
      "Remove mouth clicks (final pass)",
      "A gentle second click pass after voice polish, catching clicks that were uncovered.",
    ),
    silence: op(
      "Cut silence",
      "Removes the silent parts so only the speech is left (or just shortens long pauses).",
      {
        action: "Action",
        min_silence_s: "Shortest silence to cut",
        keep_pause_s: "Pause length after shortening",
        mode: "Detection",
        pad_s: "Keep around speech",
        threshold_db: "Silence threshold",
      },
      {
        action: { remove: "Remove silence", shorten: "Shorten pauses" },
        mode: { vad: "Speech detection", amplitude: "Volume threshold" },
      },
    ),
    speed: op(
      "Change speed",
      "Faster or slower without the chipmunk effect.",
      { factor: "Speed" },
    ),
    loudness: op(
      "Even out volume",
      "Sets a steady listening level (Podcast −16 / YouTube −14) and stops clipping.",
      { target: "Target", lufs: "Level", true_peak_db: "True peak" },
      {
        target: {
          podcast: "Podcast (−16 LUFS)",
          youtube: "YouTube (−14 LUFS)",
          custom: "Custom",
        },
      },
    ),
    subtitles: op(
      "Create subtitles",
      "Writes timed captions (SRT/VTT) from the final audio.",
      {
        language: "Language",
        format: "Format",
        model: "Model",
        max_line_chars: "Longest line",
        max_lines: "Lines per caption",
      },
      {
        language: {
          auto: "Auto-detect",
          en: "English",
          ar: "Arabic",
          fr: "French",
          de: "German",
          es: "Spanish",
          tr: "Turkish",
          ur: "Urdu",
        },
        format: { srt: "SRT", vtt: "VTT", both: "Both" },
        model: { fast: "Fast", accurate: "Accurate" },
      },
    ),
  },
  msg: {
    silence_removed: {
      text: "Removed {seconds} s of silence in {places}",
      plurals: {
        places: {
          count: "count",
          forms: {
            zero: "{n} places",
            one: "{n} place",
            two: "{n} places",
            few: "{n} places",
            many: "{n} places",
            other: "{n} places",
          },
        },
      },
    },
    pauses_shortened: {
      text: "Shortened {pauses}, saved {seconds} s",
      plurals: {
        pauses: {
          count: "count",
          forms: {
            zero: "{n} pauses",
            one: "{n} pause",
            two: "{n} pauses",
            few: "{n} pauses",
            many: "{n} pauses",
            other: "{n} pauses",
          },
        },
      },
    },
    no_speech_found:
      "No speech found, so nothing was cut. Try Volume threshold mode.",
    very_quiet_raised:
      "Very quiet recording — raised as far as safely possible",
    noise_reduced: "Cut background noise by about {db} dB",
    room_resonances_cut: "Reduced room echo: cut resonances at {freqs} Hz",
    breaths_reduced: {
      text: "Made {breaths} quieter by about {db} dB",
      zero: "No breaths found",
      plurals: {
        breaths: {
          count: "count",
          forms: {
            zero: "{n} breaths",
            one: "{n} breath",
            two: "{n} breaths",
            few: "{n} breaths",
            many: "{n} breaths",
            other: "{n} breaths",
          },
        },
      },
    },
    clicks_removed: {
      text: "Removed {clicks}",
      zero: "No mouth clicks found",
      plurals: {
        clicks: {
          count: "count",
          forms: {
            zero: "{n} mouth clicks",
            one: "{n} mouth click",
            two: "{n} mouth clicks",
            few: "{n} mouth clicks",
            many: "{n} mouth clicks",
            other: "{n} mouth clicks",
          },
        },
      },
    },
    separated: "Separated voice and music ({output})",
    subtitles_written: "Wrote {count} subtitle lines ({language})",
    language_detected: "Detected language: {language}",
    trim_outside_preview:
      "The trim range is outside the previewed part — nothing was trimmed.",
    gpu_fallback:
      "Graphics memory ran out. Running this step on the processor instead.",
    nvenc_fallback: "Graphics encoder failed — encoded on the processor instead",
    model_downloading: "Downloading {model_id} ({pct} %)",
    preset_partial: "Some steps were skipped: {skipped_op_ids}",
    job_queued: "Queued — position {position}",
    job_done: "Done",
    done_files: {
      text: "Done · {files}",
      plurals: {
        files: {
          count: "count",
          forms: {
            zero: "{n} files",
            one: "{n} file",
            two: "{n} files",
            few: "{n} files",
            many: "{n} files",
            other: "{n} files",
          },
        },
      },
    },
    job_interrupted: "Stopped because the app was closed",
    job_cancelled: "Cancelled",
    job_failed: "The job failed",
    prep_failed: "We couldn't read the audio in this file.",
    insufficient_storage:
      "Not enough disk space: needs about {needed_gb} GB, {free_gb} GB free.",
    unsupported_type: "This file type isn't supported. Try WAV, MP3, MP4…",
    unreadable:
      "We couldn't read this file. It may be damaged or not a media file.",
    no_audio: "This video has no audio track. Choose another file.",
    gpu_unavailable: "The graphics card isn't available on this computer.",
    too_large: "This file is too large.",
    media_not_found: "This file was deleted or never existed.",
    job_not_found: "This job was deleted or never existed.",
    bad_request: "Invalid request.",
    bad_param: "Invalid setting: {param}",
    bad_device: "Invalid device.",
    bad_output: "Unknown audio format.",
    bad_preview: "The preview must be between 1 and 60 seconds.",
    no_such_stream: "This file has no audio track with that number.",
    not_terminal: "This job is still active.",
    not_retryable:
      "Only failed, cancelled or interrupted jobs can be retried.",
    timeline_not_found: "The timeline isn't available for this job yet.",
    peaks_not_found: "The waveform isn't available for this job yet.",
    saved_to: "Saved to {path}",
    output_dir_set: "Results will be saved to {path}",
    output_dir_invalid: "This folder can't be used. Choose another one.",
    picker_unavailable:
      "The folder window couldn't open on this computer.",
    picker_busy: "A folder window is already open.",
    auto_save_failed: "Couldn't save to {path}. Save it manually.",
    save_failed: "Couldn't save the file. Try again.",
    no_saved_path: "This file wasn't saved to a folder.",
    in_use: "A running job is using this file.",
    processing: "Still preparing this file.",
    client_disconnected: "The upload was interrupted.",
    bad_host: "This app only answers on this computer.",
    bad_origin: "This request didn't come from this app.",
    karaoke_speech_op: "Off while Karaoke is on",
    planned: "Coming later",
    not_implemented: "Coming in a later update",
    module_missing: "Not available on this computer",
    model_missing: "Model not downloaded yet",
    unavailable: "Not available on this computer",
    aborted: "The upload was cancelled.",
    network: "Network error — is the app still running?",
  } satisfies Record<string, MsgVal>,
};

/** `msg` is widened to Record so the Arabic dictionary may attach fuller
 *  plural-form objects (zero/two/few/many) than English needs. */
export type Dict = Omit<typeof en, "msg"> & { msg: Record<string, MsgVal> };
