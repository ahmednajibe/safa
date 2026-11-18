/** Shared flows: upload, job submit/cancel/retry, streaming saves.
 *  Kept out of components so Dock / PlayerPanel / shortcuts call the same
 *  code paths. */

import {
  cancelJob,
  createJob,
  deleteMedia,
  getJob,
  getMedia,
  retryJob,
  saveAllOnServer,
  saveJobFileOnServer,
  uploadMedia,
} from "./api/client";
import { ApiError } from "./api/types";
import type { Job, MediaInfo } from "./api/types";
import { downloadJobFile } from "./lib/save";
import { isMediaReady } from "./lib/media";
import type { Store } from "./state/store";

let uploadAbort: (() => void) | null = null;

export function abortUpload(): void {
  uploadAbort?.();
  uploadAbort = null;
}

export function startUpload(store: Store, file: File): void {
  const { dispatchMedia, dispatchJob, dispatchSelection } = store;
  dispatchMedia({
    type: "upload-start",
    filename: file.name,
    sizeBytes: file.size,
  });
  dispatchJob({ type: "clear" });
  dispatchSelection({ type: "clear" });
  const { promise, abort } = uploadMedia(file, (sent) => {
    dispatchMedia({ type: "upload-progress", uploadedBytes: sent });
  });
  uploadAbort = abort;
  promise
    .then((media) => {
      uploadAbort = null;
      dispatchMedia({ type: "uploaded", media });
    })
    .catch((e: unknown) => {
      uploadAbort = null;
      if (e instanceof ApiError && e.code === "aborted") {
        dispatchMedia({ type: "clear" });
        return;
      }
      const err = e as ApiError;
      dispatchMedia({
        type: "media-error",
        code: err.code ?? "unreadable",
        message: err.message ?? "upload failed",
        params: err.params,
      });
    });
}

export function removeMedia(store: Store): void {
  const id = store.media.mediaId;
  abortUpload();
  if (id) {
    deleteMedia(id).catch(() => {});
  }
  store.dispatchMedia({ type: "clear" });
  store.dispatchJob({ type: "clear" });
  store.dispatchSelection({ type: "clear" });
}

export interface SubmitOpts {
  preview?: { start_s: number; end_s: number };
}

export async function submitJob(
  store: Store,
  opts: SubmitOpts = {},
): Promise<Job | null> {
  const { media, selection, ui, dispatchJob } = store;
  if (!media.mediaId) return null;
  try {
    const job = await createJob({
      media_id: media.mediaId,
      ops: selection.effective.map((o) => ({ id: o.id, params: o.params })),
      output: { audio_format: ui.format, keep_video: ui.keepVideo },
      device: ui.device,
      preview: opts.preview ?? null,
      audio_stream: media.audioStream,
    });
    dispatchJob({ type: "job-created", job });
    if (opts.preview) {
      dispatchJob({
        type: "preview-submitted",
        fingerprint: JSON.stringify(selection.effective),
      });
    }
    return job;
  } catch (e) {
    const err = e as ApiError;
    dispatchJob({
      type: "save-error",
      error: { code: err.code ?? "bad_request", params: err.params ?? {} },
    });
    return null;
  }
}

export async function cancelActiveJob(store: Store): Promise<void> {
  const id = store.job.activeId;
  if (!id) return;
  try {
    const job = await cancelJob(id);
    store.dispatchJob({ type: "job-update", job });
  } catch {
    /* SSE will report the settled state */
  }
}

export async function retryFailedJob(store: Store): Promise<void> {
  const id = store.job.activeId;
  if (!id) return;
  try {
    const job = await retryJob(id);
    store.dispatchJob({ type: "job-created", job });
  } catch {
    /* stay on the failed card */
  }
}

/** Adopt a job from History or a cross-tab broadcast: the dock's job
 *  belongs to the loaded media (lib/session.ts), so a foreign job first
 *  swaps its own file in. When that media was deleted (retention) the
 *  media state is cleared instead — never pair one file's output with
 *  another's original (R2 #13c). */
export async function adoptJob(
  store: Pick<Store, "media" | "dispatchMedia" | "dispatchJob">,
  j: Job,
  fetchMedia: (id: string) => Promise<MediaInfo> = getMedia,
): Promise<void> {
  const { media, dispatchMedia, dispatchJob } = store;
  if (j.media_id !== media.mediaId) {
    try {
      const m = await fetchMedia(j.media_id);
      if (m.ready?.error) {
        dispatchMedia({
          type: "media-error",
          code: "prep_failed",
          message: m.ready.error,
        });
      } else if (isMediaReady(m)) {
        dispatchMedia({ type: "media-ready", media: m });
      } else {
        dispatchMedia({ type: "uploaded", media: m });
      }
    } catch {
      dispatchMedia({ type: "clear" });
    }
  }
  dispatchJob({ type: "set-active", job: j });
}

/* ---------- saving ---------- */

/** Show the job's fresh `files` (saved_path → "Show in folder"). */
async function refreshSavedFiles(store: Store, jobId: string): Promise<void> {
  if (store.job.activeId !== jobId) return; // History refreshes on open
  try {
    store.dispatchJob({ type: "job-update", job: await getJob(jobId) });
  } catch {
    /* the save itself succeeded */
  }
}

function serverSaveFailed(
  store: Store,
  jobId: string,
  e: unknown,
): "fallback" | "handled" {
  const code = (e as ApiError)?.code;
  if (code === "picker_unavailable") return "fallback";
  store.dispatchJob({
    type: "save-error",
    jobId,
    error: { code: code || "save_failed", params: {} },
  });
  return "handled";
}

/** "Save X…" for one job output: the local server opens the native Save As
 *  window and copies the file (works in every browser). No GUI on the
 *  server (Docker/headless) → plain browser download instead. */
export async function saveOne(
  store: Store,
  jobId: string,
  file: { name: string; size_bytes: number },
): Promise<void> {
  const { dispatchJob } = store;
  if (store.job.saving || store.job.savingAll) return; // one save at a time
  // pct -1 = the native window is open ("Choose where to save…")
  dispatchJob({ type: "saving", jobId, name: file.name, pct: -1 });
  try {
    const r = await saveJobFileOnServer(jobId, file.name);
    if (r.status === "saved") {
      dispatchJob({ type: "saved", jobId, name: file.name });
      await refreshSavedFiles(store, jobId);
    } else {
      dispatchJob({ type: "save-error", jobId, error: null }); // cancelled
    }
    return;
  } catch (e) {
    if (serverSaveFailed(store, jobId, e) === "handled") return;
  }
  downloadJobFile(jobId, file.name);
  dispatchJob({ type: "saved", jobId, name: file.name });
}

/** "Save all to folder…": native folder window on the server, every output
 *  copied in (never overwriting: `<stem> (2).<ext>`). */
export async function saveAll(
  store: Store,
  jobId: string,
  files: { name: string; size_bytes: number }[],
): Promise<void> {
  const { dispatchJob } = store;
  if (store.job.saving || store.job.savingAll) return; // one save at a time
  dispatchJob({
    type: "saving-all",
    jobId,
    index: 0,
    total: files.length,
    name: files[0]?.name ?? "",
    pct: -1,
  });
  try {
    const r = await saveAllOnServer(jobId);
    if (r.status === "saved") {
      dispatchJob({ type: "saved-all" });
      for (const f of files) dispatchJob({ type: "saved", jobId, name: f.name });
      await refreshSavedFiles(store, jobId);
    } else {
      dispatchJob({ type: "save-error", jobId, error: null });
    }
    return;
  } catch (e) {
    if (serverSaveFailed(store, jobId, e) === "handled") return;
  }
  for (const f of files) downloadJobFile(jobId, f.name);
  dispatchJob({ type: "saved-all" });
  for (const f of files) dispatchJob({ type: "saved", jobId, name: f.name });
}
