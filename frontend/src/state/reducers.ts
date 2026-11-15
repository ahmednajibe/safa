/** Pure reducers for the app store (M9a) — media / selection / job /
 *  settings / ui. Exported individually so unit tests drive them without
 *  React. */

import type { DisabledOp, Job, MediaInfo, Preset } from "../api/types";

export type Lang = "en" | "ar";
export type Device = "gpu" | "cpu";
export type AudioFormat = "wav" | "flac" | "mp3" | "aac";

/* ---------- media ---------- */

export type MediaStatus =
  | "empty"
  | "uploading"
  | "preparing"
  | "ready"
  | "error";

export interface MediaState {
  status: MediaStatus;
  mediaId: string | null;
  filename: string | null;
  sizeBytes: number;
  probe: MediaInfo["probe"] | null;
  lufs: number | null;
  uploadedBytes: number;
  error: {
    code: string;
    message: string;
    params: Record<string, unknown>;
  } | null;
  audioStream: number | null;
}

export const emptyMedia: MediaState = {
  status: "empty",
  mediaId: null,
  filename: null,
  sizeBytes: 0,
  probe: null,
  lufs: null,
  uploadedBytes: 0,
  error: null,
  audioStream: null,
};

export type MediaAction =
  | { type: "upload-start"; filename: string; sizeBytes: number }
  | { type: "upload-progress"; uploadedBytes: number }
  | { type: "uploaded"; media: MediaInfo }
  | { type: "media-ready"; media: MediaInfo }
  | {
      type: "media-error";
      code: string;
      message: string;
      params?: Record<string, unknown>;
    }
  | { type: "set-stream"; index: number | null }
  | { type: "clear" };

function fromInfo(s: MediaState, m: MediaInfo, status: MediaStatus): MediaState {
  return {
    ...s,
    status,
    mediaId: m.media_id,
    filename: m.filename,
    sizeBytes: m.size_bytes,
    probe: m.probe,
    lufs: m.lufs ?? null,
    error: null,
  };
}

export function mediaReducer(s: MediaState, a: MediaAction): MediaState {
  switch (a.type) {
    case "upload-start":
      return {
        ...emptyMedia,
        status: "uploading",
        filename: a.filename,
        sizeBytes: a.sizeBytes,
      };
    case "upload-progress":
      return s.status === "uploading"
        ? { ...s, uploadedBytes: a.uploadedBytes }
        : s;
    case "uploaded":
      return fromInfo(s, a.media, "preparing");
    case "media-ready":
      return fromInfo(s, a.media, "ready");
    case "media-error":
      return {
        ...s,
        status: "error",
        error: { code: a.code, message: a.message, params: a.params ?? {} },
      };
    case "set-stream":
      return { ...s, audioStream: a.index };
    case "clear":
      return emptyMedia;
    default:
      return s;
  }
}

/* ---------- selection ---------- */

export interface SelectionState {
  /** raw user selection {op_id: params} */
  ops: Record<string, Record<string, unknown>>;
  /** last resolve() result: effective ordered list + disabled map */
  effective: { id: string; params: Record<string, unknown> }[];
  disabled: Record<string, DisabledOp>;
  /** last applied preset (for auto-scroll + dirty tracking) */
  appliedPreset: string | null;
}

export const emptySelection: SelectionState = {
  ops: {},
  effective: [],
  disabled: {},
  appliedPreset: null,
};

export type SelAction =
  | { type: "toggle"; opId: string }
  | { type: "set-param"; opId: string; key: string; value: unknown }
  | { type: "reset-params"; opId: string }
  | {
      type: "apply-preset";
      presetId: string;
      ops: Record<string, Record<string, unknown>>;
    }
  | { type: "off" }
  | {
      type: "resolved";
      ops: { id: string; params: Record<string, unknown> }[];
      disabled: Record<string, DisabledOp>;
    }
  | { type: "clear" };

export function selectionReducer(s: SelectionState, a: SelAction): SelectionState {
  switch (a.type) {
    case "toggle": {
      const ops = { ...s.ops };
      if (a.opId in ops) delete ops[a.opId];
      else ops[a.opId] = {};
      return { ...s, ops };
    }
    case "set-param": {
      if (!(a.opId in s.ops)) return s;
      return {
        ...s,
        ops: { ...s.ops, [a.opId]: { ...s.ops[a.opId], [a.key]: a.value } },
      };
    }
    case "reset-params": {
      if (!(a.opId in s.ops)) return s;
      return { ...s, ops: { ...s.ops, [a.opId]: {} } };
    }
    case "apply-preset":
      return { ...s, ops: { ...a.ops }, appliedPreset: a.presetId };
    case "off":
      return { ...s, ops: {}, appliedPreset: null };
    case "resolved":
      return { ...s, effective: a.ops, disabled: a.disabled };
    case "clear":
      return emptySelection;
    default:
      return s;
  }
}

function opsEqual(
  a: Record<string, Record<string, unknown>>,
  b: Record<string, Record<string, unknown>>,
): boolean {
  const ak = Object.keys(a);
  const bk = Object.keys(b);
  if (ak.length !== bk.length) return false;
  return ak.every(
    (k) => k in b && JSON.stringify(a[k]) === JSON.stringify(b[k]),
  );
}

/** Which preset the current ops equal, or null (= Custom / empty). */
export function activePresetId(
  presets: Record<string, Preset>,
  ops: Record<string, Record<string, unknown>>,
): string | null {
  if (!Object.keys(ops).length) return null;
  for (const [id, p] of Object.entries(presets)) {
    if (opsEqual(ops, p.ops)) return id;
  }
  return null;
}

/* ---------- job ---------- */

export interface PreviewRegion {
  start_s: number;
  end_s: number;
}

export interface JobState {
  /** job whose SSE the dock follows */
  activeId: string | null;
  job: Job | null;
  /** last completed job feeding the A/B "Processed" side */
  compareJobId: string | null;
  compareKind: string | null;
  previewRegion: PreviewRegion;
  /** ops snapshot when the last preview was submitted (stale check) */
  previewFingerprint: string | null;
  /** per-file "Saved" marks for the active job */
  saved: Record<string, boolean>;
  saving: { name: string; pct: number } | null;
  savingAll: { index: number; total: number; name: string; pct: number } | null;
  saveError: { code: string; params: Record<string, unknown> } | null;
}

export const emptyJob: JobState = {
  activeId: null,
  job: null,
  compareJobId: null,
  compareKind: null,
  previewRegion: { start_s: 0, end_s: 20 },
  previewFingerprint: null,
  saved: {},
  saving: null,
  savingAll: null,
  saveError: null,
};

export type JobAction =
  | { type: "job-created"; job: Job }
  | { type: "job-update"; job: Job }
  | { type: "set-active"; job: Job | null }
  | { type: "preview-region"; region: PreviewRegion }
  | { type: "preview-submitted"; fingerprint: string }
  | { type: "saving"; name: string; pct: number }
  | { type: "saving-all"; index: number; total: number; name: string; pct: number }
  | { type: "saved"; name: string }
  | { type: "saved-all" }
  | {
      type: "save-error";
      error: { code: string; params: Record<string, unknown> } | null;
    }
  | { type: "set-compare"; jobId: string | null; kind: string | null }
  | { type: "hydrate-latest"; job: Job }
  | { type: "clear" };

const TERMINAL = new Set(["completed", "failed", "cancelled", "interrupted"]);

export function jobReducer(s: JobState, a: JobAction): JobState {
  switch (a.type) {
    case "job-created":
      return {
        ...s,
        activeId: a.job.id,
        job: a.job,
        saved: {},
        saving: null,
        savingAll: null,
        saveError: null,
      };
    case "job-update": {
      const compare =
        a.job.state === "completed"
          ? { compareJobId: a.job.id, compareKind: a.job.kind }
          : {};
      return { ...s, job: a.job, ...compare };
    }
    case "set-active":
      return {
        ...s,
        activeId: a.job ? a.job.id : null,
        job: a.job,
        compareJobId:
          a.job && a.job.state === "completed"
            ? a.job.id
            : s.compareJobId,
        compareKind:
          a.job && a.job.state === "completed" ? a.job.kind : s.compareKind,
      };
    case "preview-region":
      return { ...s, previewRegion: a.region };
    case "preview-submitted":
      return { ...s, previewFingerprint: a.fingerprint };
    case "saving":
      return { ...s, saving: { name: a.name, pct: a.pct }, saveError: null };
    case "saving-all":
      return {
        ...s,
        savingAll: {
          index: a.index,
          total: a.total,
          name: a.name,
          pct: a.pct,
        },
        saveError: null,
      };
    case "saved": {
      const saved = { ...s.saved, [a.name]: true };
      return { ...s, saved, saving: null, saveError: null };
    }
    case "saved-all":
      return { ...s, savingAll: null, saveError: null };
    case "save-error":
      return { ...s, saveError: a.error, saving: null, savingAll: null };
    case "set-compare":
      return { ...s, compareJobId: a.jobId, compareKind: a.kind };
    case "hydrate-latest":
      // Only adopt when nothing is tracked yet (session resume found no
      // active job) — never clobber a live dock.
      if (s.activeId) return s;
      return {
        ...s,
        activeId: a.job.id,
        job: a.job,
        compareJobId:
          a.job.state === "completed" ? a.job.id : s.compareJobId,
        compareKind:
          a.job.state === "completed" ? a.job.kind : s.compareKind,
      };
    case "clear":
      return { ...emptyJob, previewRegion: s.previewRegion };
    default:
      return s;
  }
}

export function isTerminal(job: Job | null): boolean {
  return job !== null && TERMINAL.has(job.state);
}

/* ---------- settings ---------- */

export interface SettingsState {
  outputDir: string | null;
  loaded: boolean;
}

export const emptySettings: SettingsState = { outputDir: null, loaded: false };

export type SettingsAction =
  | { type: "settings"; outputDir: string | null }
  | { type: "clear" };

export function settingsReducer(
  s: SettingsState,
  a: SettingsAction,
): SettingsState {
  switch (a.type) {
    case "settings":
      return { outputDir: a.outputDir, loaded: true };
    case "clear":
      return emptySettings;
    default:
      return s;
  }
}

/* ---------- ui ---------- */

export interface UiState {
  lang: Lang;
  historyOpen: boolean;
  settingsOpen: boolean;
  shortcutsOpen: boolean;
  device: Device;
  format: AudioFormat;
  keepVideo: boolean;
  /** rack expanded on narrow screens */
  rackOpen: boolean;
}

export const emptyUi: UiState = {
  lang: "en",
  historyOpen: false,
  settingsOpen: false,
  shortcutsOpen: false,
  device: "gpu",
  format: "wav",
  keepVideo: true,
  rackOpen: false,
};

export type UiAction =
  | { type: "lang"; lang: Lang }
  | { type: "history"; open: boolean }
  | { type: "settings-pop"; open: boolean }
  | { type: "shortcuts-pop"; open: boolean }
  | { type: "device"; device: Device }
  | { type: "output"; format?: AudioFormat; keepVideo?: boolean }
  | { type: "rack"; open: boolean };

export function uiReducer(s: UiState, a: UiAction): UiState {
  switch (a.type) {
    case "lang":
      return { ...s, lang: a.lang };
    case "history":
      return { ...s, historyOpen: a.open, settingsOpen: false, shortcutsOpen: false };
    case "settings-pop":
      return { ...s, settingsOpen: a.open, shortcutsOpen: false };
    case "shortcuts-pop":
      return { ...s, shortcutsOpen: a.open, settingsOpen: false };
    case "device":
      return { ...s, device: a.device };
    case "output":
      return {
        ...s,
        format: a.format ?? s.format,
        keepVideo: a.keepVideo ?? s.keepVideo,
      };
    case "rack":
      return { ...s, rackOpen: a.open };
    default:
      return s;
  }
}
