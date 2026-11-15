/** SPEC §12 saving: File System Access streaming with counted progress —
 * never res.blob() (multi-GB outputs stream straight to disk). The picker
 * call MUST be the first await in the click path (user-gesture rule). */

import { jobFileUrl } from "../api/client";

export type SaveOutcome = "saved" | "aborted" | "error";

export function canPickFiles(): boolean {
  return typeof window !== "undefined" && "showSaveFilePicker" in window;
}

export function canPickDir(): boolean {
  return typeof window !== "undefined" && "showDirectoryPicker" in window;
}

function isAbort(e: unknown): boolean {
  return e instanceof DOMException && e.name === "AbortError";
}

function countStream(
  total: number,
  onPct: (pct: number) => void,
): TransformStream<Uint8Array, Uint8Array> {
  let sent = 0;
  return new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, ctl) {
      sent += chunk.byteLength;
      if (total > 0) onPct(Math.min(1, sent / total));
      ctl.enqueue(chunk);
    },
  });
}

async function fetchStream(url: string): Promise<ReadableStream<Uint8Array>> {
  const res = await fetch(url);
  if (!res.ok || !res.body) throw new Error(`download failed: ${res.status}`);
  return res.body;
}

const KIND_MIME: Record<string, [string, string]> = {
  ".mp4": ["video/mp4", ".mp4"],
  ".mkv": ["video/x-matroska", ".mkv"],
  ".mov": ["video/quicktime", ".mov"],
  ".wav": ["audio/wav", ".wav"],
  ".flac": ["audio/flac", ".flac"],
  ".mp3": ["audio/mpeg", ".mp3"],
  ".aac": ["audio/aac", ".aac"],
  ".m4a": ["audio/mp4", ".m4a"],
  ".srt": ["application/x-subrip", ".srt"],
  ".vtt": ["text/vtt", ".vtt"],
};

function pickerTypes(
  name: string,
): { description?: string; accept: Record<string, string[]> }[] | undefined {
  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  const hit = KIND_MIME[ext];
  if (!hit) return undefined;
  return [{ accept: { [hit[0]]: [hit[1]] } }];
}

/** "Save as…" one job output. `window.showSaveFilePicker` is invoked before
 *  any other await so transient user activation is still alive (SPEC §12). */
export async function saveJobFileAs(
  jobId: string,
  name: string,
  sizeBytes: number,
  onPct: (pct: number) => void,
): Promise<SaveOutcome> {
  // Picker FIRST — do not add awaits above this line.
  let handle: FileSystemFileHandle;
  try {
    handle = await window.showSaveFilePicker({
      id: "ss-save",
      suggestedName: name,
      types: pickerTypes(name),
    });
  } catch (e) {
    if (isAbort(e)) return "aborted";
    return "error";
  }
  let writable: FileSystemWritableFileStream | null = null;
  try {
    writable = await handle.createWritable();
    const body = await fetchStream(jobFileUrl(jobId, name));
    await body.pipeThrough(countStream(sizeBytes, onPct)).pipeTo(writable);
    writable = null;
    return "saved";
  } catch (e) {
    try {
      await writable?.abort();
    } catch {
      /* nothing to undo */
    }
    return isAbort(e) ? "aborted" : "error";
  }
}

/** Fallback where the File System Access API doesn't exist: plain download. */
export function downloadJobFile(jobId: string, name: string): void {
  const a = document.createElement("a");
  a.href = jobFileUrl(jobId, name);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function freeName(
  dir: FileSystemDirectoryHandle,
  name: string,
): Promise<string> {
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot) : "";
  for (let i = 1; i < 10000; i++) {
    const cand = i === 1 ? name : `${stem} (${i})${ext}`;
    try {
      await dir.getFileHandle(cand);
    } catch {
      return cand; // NotFound → free
    }
  }
  return `${stem}-new${ext}`;
}

/** "Save all to folder…": every output into one picked directory, never
 *  overwriting (`<stem> (2).<ext>`). */
export async function saveAllToFolder(
  jobId: string,
  files: { name: string; size_bytes: number }[],
  onFile: (name: string, index: number) => void,
  onPct: (pct: number) => void,
): Promise<SaveOutcome> {
  // Picker FIRST — keep it the first await on the click path.
  let dir: FileSystemDirectoryHandle;
  try {
    dir = await window.showDirectoryPicker({ id: "ss-all", mode: "readwrite" });
  } catch (e) {
    if (isAbort(e)) return "aborted";
    return "error";
  }
  try {
    for (const [i, f] of files.entries()) {
      onFile(f.name, i);
      const target = await freeName(dir, f.name);
      const handle = await dir.getFileHandle(target, { create: true });
      const writable = await handle.createWritable();
      try {
        const body = await fetchStream(jobFileUrl(jobId, f.name));
        await body
          .pipeThrough(countStream(f.size_bytes, onPct))
          .pipeTo(writable);
      } catch (e) {
        try {
          await writable.abort();
        } catch {
          /* nothing to undo */
        }
        throw e;
      }
    }
    return "saved";
  } catch (e) {
    return isAbort(e) ? "aborted" : "error";
  }
}
