import type {
  AppSettings,
  Estimate,
  Health,
  Job,
  JobsResponse,
  MediaInfo,
  OperationsResponse,
  ResolveResponse,
  TimelineData,
} from "./types";
import { ApiError } from "./types";

async function readError(r: Response): Promise<ApiError> {
  try {
    const body = (await r.json()) as {
      code?: string;
      error?: string;
      message?: string;
      detail?: string;
      params?: Record<string, unknown>;
    };
    const code = body.code ?? body.error ?? `${r.status}`;
    const message = body.message ?? body.detail ?? `${r.status}`;
    return new ApiError(code, message, body.params ?? {});
  } catch {
    return new ApiError(`${r.status}`, `${r.status}`);
  }
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!r.ok) throw await readError(r);
  return (await r.json()) as T;
}

async function sendJson<T>(
  method: string,
  url: string,
  body?: unknown,
): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? null : JSON.stringify(body),
  });
  if (!r.ok) throw await readError(r);
  return (await r.json()) as T;
}

export function getHealth(): Promise<Health> {
  return getJson<Health>("/api/health");
}

export function getOperations(): Promise<OperationsResponse> {
  return getJson<OperationsResponse>("/api/operations");
}

export function resolveOps(
  ops: Record<string, Record<string, unknown>>,
  signal?: AbortSignal,
): Promise<ResolveResponse> {
  return (async () => {
    const r = await fetch("/api/operations/resolve", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ ops }),
      signal,
    });
    if (!r.ok) throw await readError(r);
    return (await r.json()) as ResolveResponse;
  })();
}

export function getMedia(id: string): Promise<MediaInfo> {
  return getJson<MediaInfo>(`/api/media/${id}`);
}

export function deleteMedia(id: string): Promise<Response> {
  return fetch(`/api/media/${id}`, { method: "DELETE" }).then(async (r) => {
    if (!r.ok) throw await readError(r);
    return r;
  });
}

/** Raw streaming upload: octet-stream body, filename in X-Filename.
 *  Reports progress through cb(bytesSent, total). The returned promise
 *  resolves with the created media payload; abort() cancels the XHR. */
export function uploadMedia(
  file: File,
  cb: (sent: number, total: number) => void,
): { promise: Promise<MediaInfo>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<MediaInfo>((resolve, reject) => {
    xhr.open("POST", "/api/media");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) cb(e.loaded, e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as MediaInfo);
        } catch {
          reject(new ApiError("bad_request", "Bad response"));
        }
        return;
      }
      try {
        const body = JSON.parse(xhr.responseText) as {
          code?: string;
          error?: string;
          message?: string;
          params?: Record<string, unknown>;
        };
        reject(
          new ApiError(
            body.code ?? body.error ?? `${xhr.status}`,
            body.message ?? `${xhr.status}`,
            body.params ?? {},
          ),
        );
      } catch {
        reject(new ApiError(`${xhr.status}`, `${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new ApiError("network", "Network error"));
    xhr.onabort = () => reject(new ApiError("aborted", "Upload cancelled"));
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() };
}

export function createJob(body: {
  media_id: string;
  ops: Record<string, Record<string, unknown>> | { id: string; params: Record<string, unknown> }[];
  output: { audio_format: string; keep_video: boolean };
  device: "gpu" | "cpu";
  preview?: { start_s: number; end_s: number } | null;
  audio_stream?: number | null;
}): Promise<Job> {
  return sendJson<Job>("POST", "/api/jobs", body);
}

export function getJobs(): Promise<JobsResponse> {
  return getJson<JobsResponse>("/api/jobs");
}

export function getJob(id: string): Promise<Job> {
  return getJson<Job>(`/api/jobs/${id}`);
}

export function cancelJob(id: string): Promise<Job> {
  return sendJson<Job>("POST", `/api/jobs/${id}/cancel`);
}

export function retryJob(id: string): Promise<Job> {
  return sendJson<Job>("POST", `/api/jobs/${id}/retry`);
}

export function deleteJob(id: string): Promise<Response> {
  return fetch(`/api/jobs/${id}`, { method: "DELETE" }).then(async (r) => {
    if (!r.ok) throw await readError(r);
    return r;
  });
}

export function getTimeline(id: string): Promise<TimelineData> {
  return getJson<TimelineData>(`/api/jobs/${id}/timeline`);
}

export function getEstimate(
  mediaId: string,
  ops: { id: string; params: Record<string, unknown> }[],
  device: "gpu" | "cpu",
  signal?: AbortSignal,
): Promise<Estimate> {
  const q = new URLSearchParams({
    media_id: mediaId,
    device,
    ops: JSON.stringify(ops),
  });
  return getJson<Estimate>(`/api/estimate?${q}`, signal);
}

export function getSettings(): Promise<AppSettings> {
  return getJson<AppSettings>("/api/settings");
}

export function putOutputDir(path: string): Promise<{
  output_dir: string;
  message_code: string;
  message_params: Record<string, unknown>;
}> {
  return sendJson("PUT", "/api/settings/output-dir", { path });
}

export function deleteOutputDir(): Promise<Response> {
  return fetch("/api/settings/output-dir", { method: "DELETE" }).then(
    async (r) => {
      if (!r.ok) throw await readError(r);
      return r;
    },
  );
}

export function pickOutputDir(): Promise<{
  cancelled: boolean;
  output_dir?: string | null;
}> {
  return sendJson("POST", "/api/settings/output-dir/pick");
}

export function revealSaved(jobId: string, name: string): Promise<unknown> {
  return sendJson(
    "POST",
    `/api/jobs/${id_encode(jobId)}/files/${encodeURIComponent(name)}/reveal`,
  );
}

export function revealOutputDir(): Promise<unknown> {
  return sendJson("POST", "/api/settings/output-dir/reveal");
}

function id_encode(id: string): string {
  return encodeURIComponent(id);
}

export function jobFileUrl(jobId: string, name: string): string {
  return `/api/jobs/${encodeURIComponent(jobId)}/files/${encodeURIComponent(name)}`;
}

export function mediaPlaybackUrl(mediaId: string): string {
  return `/api/media/${encodeURIComponent(mediaId)}/playback`;
}
