/** Shared flows: upload, job submit/cancel/retry, streaming saves.
 *  Kept out of components so Dock / PlayerPanel / shortcuts call the same
 *  code paths. */

import {
  cancelJob,
  createJob,
  deleteMedia,
  retryJob,
  uploadMedia,
} from "./api/client";
import { ApiError } from "./api/types";
import type { Job } from "./api/types";
import {
  canPickDir,
  canPickFiles,
  downloadJobFile,
  saveAllToFolder,
  saveJobFileAs,
} from "./lib/save";
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

/* ---------- saving ---------- */

/** "Save X…" / fallback "Download X" for one job output file. */
export async function saveOne(
  store: Store,
  jobId: string,
  file: { name: string; size_bytes: number },
): Promise<void> {
  const { dispatchJob } = store;
  if (!canPickFiles()) {
    downloadJobFile(jobId, file.name);
    dispatchJob({ type: "saved", name: file.name });
    return;
  }
  const outcome = await saveJobFileAs(jobId, file.name, file.size_bytes, (pct) =>
    dispatchJob({ type: "saving", name: file.name, pct }),
  );
  if (outcome === "saved") dispatchJob({ type: "saved", name: file.name });
  else if (outcome === "aborted") dispatchJob({ type: "save-error", error: null });
  else dispatchJob({ type: "save-error", error: { code: "save_failed", params: {} } });
}

/** "Save all to folder…" — streams every output into the picked dir. */
export async function saveAll(
  store: Store,
  jobId: string,
  files: { name: string; size_bytes: number }[],
): Promise<void> {
  const { dispatchJob } = store;
  if (!canPickDir()) {
    for (const f of files) downloadJobFile(jobId, f.name);
    dispatchJob({ type: "saved-all" });
    return;
  }
  const outcome = await saveAllToFolder(
    jobId,
    files,
    (name, i) =>
      dispatchJob({
        type: "saving-all",
        index: i,
        total: files.length,
        name,
        pct: 0,
      }),
    (pct) => {
      const cur = store.job.savingAll;
      if (cur) dispatchJob({ type: "saving-all", ...cur, pct });
    },
  );
  if (outcome === "saved") {
    dispatchJob({ type: "saved-all" });
    for (const f of files) dispatchJob({ type: "saved", name: f.name });
  } else if (outcome === "aborted") {
    dispatchJob({ type: "save-error", error: null });
  } else {
    dispatchJob({ type: "save-error", error: { code: "save_failed", params: {} } });
  }
}
