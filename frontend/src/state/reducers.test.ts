import { describe, expect, it } from "vitest";
import {
  activePresetId,
  emptyJob,
  emptyMedia,
  emptySelection,
  emptySettings,
  emptyUi,
  jobReducer,
  mediaReducer,
  selectionReducer,
  settingsReducer,
  uiReducer,
} from "./reducers";
import type { Job, MediaInfo, Preset } from "../api/types";

const MEDIA: MediaInfo = {
  media_id: "a".repeat(32),
  filename: "clip.mp4",
  size_bytes: 100,
  probe: {
    duration_s: 12,
    format_name: "mov,mp4,m4a,3gp,3g2,mj2",
    size_bytes: 100,
    audio: [
      {
        index: 1,
        codec: "aac",
        sample_rate: 48000,
        channels: 2,
        channel_layout: "stereo",
        language: null,
        title: null,
        duration_s: 12,
      },
    ],
    video: [
      {
        index: 0,
        codec: "h264",
        width: 320,
        height: 240,
        fps: 25,
        duration_s: 12,
      },
    ],
    has_video: true,
  },
  lufs: -20,
  ready: { peaks: true, playback: true, error: null },
};

const PRESETS: Record<string, Preset> = {
  karaoke: {
    name: "Karaoke",
    ops: { separate: { output: "instrumental" }, loudness: {} },
  },
  podcast: {
    name: "Podcast cleanup",
    ops: { denoise: {}, silence: { action: "remove" } },
  },
};

const JOB: Job = {
  id: "b".repeat(32),
  media_id: MEDIA.media_id,
  kind: "full",
  ops: [{ id: "denoise", params: {} }],
  output: { audio_format: "wav", keep_video: true },
  preview: null,
  requested_device: "gpu",
  audio_stream: 1,
  state: "queued",
  stage: null,
  stage_id: null,
  stage_idx: null,
  total_stages: null,
  stage_pct: null,
  overall_pct: null,
  eta_sec: null,
  device: null,
  message: null,
  error: null,
  message_code: null,
  message_params: {},
  error_code: null,
  error_params: {},
  warnings: [],
  stages: [],
  created_at: "2026-11-15T00:00:00Z",
  started_at: null,
  finished_at: null,
  retry_of: null,
  queue_position: 1,
  files: [],
};

describe("mediaReducer", () => {
  it("walks upload → preparing → ready", () => {
    let s = emptyMedia;
    s = mediaReducer(s, {
      type: "upload-start",
      filename: "clip.mp4",
      sizeBytes: 100,
    });
    expect(s.status).toBe("uploading");
    s = mediaReducer(s, { type: "upload-progress", uploadedBytes: 50 });
    expect(s.uploadedBytes).toBe(50);
    s = mediaReducer(s, { type: "uploaded", media: MEDIA });
    expect(s.status).toBe("preparing");
    expect(s.mediaId).toBe(MEDIA.media_id);
    s = mediaReducer(s, { type: "media-ready", media: MEDIA });
    expect(s.status).toBe("ready");
    expect(s.lufs).toBe(-20);
  });

  it("records errors and clears", () => {
    let s = mediaReducer(emptyMedia, {
      type: "media-error",
      code: "no_audio",
      message: "no audio",
    });
    expect(s.status).toBe("error");
    expect(s.error?.code).toBe("no_audio");
    s = mediaReducer(s, { type: "clear" });
    expect(s.status).toBe("empty");
  });

  it("selects the audio stream", () => {
    let s = mediaReducer(emptyMedia, { type: "uploaded", media: MEDIA });
    s = mediaReducer(s, { type: "set-stream", index: 7 });
    expect(s.audioStream).toBe(7);
  });
});

describe("selectionReducer", () => {
  it("toggles ops on and off", () => {
    let s = emptySelection;
    s = selectionReducer(s, { type: "toggle", opId: "denoise" });
    expect(s.ops.denoise).toEqual({});
    s = selectionReducer(s, { type: "toggle", opId: "denoise" });
    expect(s.ops.denoise).toBeUndefined();
  });

  it("applies a preset, then manual edits keep the ops", () => {
    let s = selectionReducer(emptySelection, {
      type: "apply-preset",
      presetId: "podcast",
      ops: PRESETS.podcast.ops,
    });
    expect(s.ops.silence).toEqual({ action: "remove" });
    expect(s.appliedPreset).toBe("podcast");
    s = selectionReducer(s, {
      type: "set-param",
      opId: "denoise",
      key: "strength_db",
      value: 30,
    });
    expect(s.ops.denoise.strength_db).toBe(30);
  });

  it("off empties the selection", () => {
    let s = selectionReducer(emptySelection, {
      type: "apply-preset",
      presetId: "podcast",
      ops: PRESETS.podcast.ops,
    });
    s = selectionReducer(s, { type: "off" });
    expect(Object.keys(s.ops)).toHaveLength(0);
    expect(s.appliedPreset).toBeNull();
  });

  it("stores the resolve result", () => {
    const s = selectionReducer(emptySelection, {
      type: "resolved",
      ops: [{ id: "denoise", params: { engine: "ai" } }],
      disabled: { silence: { code: "karaoke_speech_op", message: "x", blocker_op_id: "separate" } },
    });
    expect(s.effective[0].id).toBe("denoise");
    expect(s.disabled.silence.code).toBe("karaoke_speech_op");
  });
});

describe("activePresetId", () => {
  it("matches preset when ops equal it", () => {
    expect(activePresetId(PRESETS, PRESETS.karaoke.ops)).toBe("karaoke");
  });
  it("returns null for custom selections", () => {
    expect(activePresetId(PRESETS, { denoise: {}, trim: {} })).toBeNull();
    expect(activePresetId(PRESETS, {})).toBeNull();
  });
});

describe("jobReducer", () => {
  it("tracks the active job and promotes completed jobs to compare", () => {
    let s = emptyJob;
    s = jobReducer(s, { type: "job-created", job: JOB });
    expect(s.activeId).toBe(JOB.id);
    s = jobReducer(s, {
      type: "job-update",
      job: { ...JOB, state: "completed" },
    });
    expect(s.job?.state).toBe("completed");
    expect(s.compareJobId).toBe(JOB.id);
  });

  it("marks saved files and save progress", () => {
    let s = jobReducer(emptyJob, { type: "job-created", job: JOB });
    s = jobReducer(s, { type: "saving", name: "a.wav", pct: 0.4 });
    expect(s.saving).toEqual({ name: "a.wav", pct: 0.4 });
    s = jobReducer(s, { type: "saved", name: "a.wav" });
    expect(s.saved["a.wav"]).toBe(true);
    expect(s.saving).toBeNull();
  });

  it("preview region updates", () => {
    let s = jobReducer(emptyJob, {
      type: "preview-region",
      region: { start_s: 2, end_s: 8 },
    });
    expect(s.previewRegion).toEqual({ start_s: 2, end_s: 8 });
  });
});

describe("settingsReducer / uiReducer", () => {
  it("settings stores the output dir", () => {
    const s = settingsReducer(emptySettings, {
      type: "settings",
      outputDir: "D:\\out",
    });
    expect(s.outputDir).toBe("D:\\out");
    expect(s.loaded).toBe(true);
  });

  it("ui toggles lang and panels", () => {
    let s = uiReducer(emptyUi, { type: "lang", lang: "ar" });
    expect(s.lang).toBe("ar");
    s = uiReducer(s, { type: "history", open: true });
    expect(s.historyOpen).toBe(true);
    s = uiReducer(s, { type: "device", device: "cpu" });
    expect(s.device).toBe("cpu");
  });
});
