import { useEffect, useRef } from "react";
import {
  getJob,
  getJobs,
  getMedia,
  getSettings,
} from "../api/client";
import type { Job } from "../api/types";
import { pickHydrationJob, SS_JOB, SS_MEDIA } from "../lib/session";
import { useStore } from "../state/store";
import type { Lang } from "../state/reducers";

const CH = "ss-sync";
const LS_LANG = "ss.lang";
const LS_DEVICE = "ss.device";

function initialLang(): Lang {
  const saved = localStorage.getItem(LS_LANG);
  if (saved === "ar" || saved === "en") return saved;
  return navigator.language.toLowerCase().startsWith("ar") ? "ar" : "en";
}

function initialDevice(): "gpu" | "cpu" {
  return localStorage.getItem(LS_DEVICE) === "cpu" ? "cpu" : "gpu";
}

/** Hydration + persistence + multi-tab sync (BroadcastChannel 'ss-sync').
 *  Mount-once: language/device prefs, settings, session active media/job,
 *  and a recent-jobs probe so a job that finished while the tab was closed
 *  still shows its result in the dock. */
export function useSync(): void {
  const {
    ui,
    media,
    job,
    dispatchUi,
    dispatchMedia,
    dispatchJob,
    dispatchSettings,
  } = useStore();
  const booted = useRef(false);
  const chan = useRef<BroadcastChannel | null>(null);

  // mount: prefs → settings → session resume → cross-tab channel
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;

    dispatchUi({ type: "lang", lang: initialLang() });
    dispatchUi({ type: "device", device: initialDevice() });

    getSettings()
      .then((s) =>
        dispatchSettings({ type: "settings", outputDir: s.output_dir }),
      )
      .catch(() => {});

    // Session resume: restore the active media and its job together —
    // or neither. A fresh tab must never show another file's finished
    // result under an empty drop zone; those live in History only.
    const mediaId = sessionStorage.getItem(SS_MEDIA);
    const jobId = sessionStorage.getItem(SS_JOB);
    if (!mediaId) {
      sessionStorage.removeItem(SS_JOB);
    } else {
      getMedia(mediaId)
        .then((m) => {
          if (m.ready?.peaks && m.lufs != null) {
            dispatchMedia({ type: "media-ready", media: m });
            return true;
          }
          if (m.ready?.error) {
            dispatchMedia({ type: "clear" });
            return false;
          }
          dispatchMedia({ type: "uploaded", media: m });
          return true;
        })
        .catch(() => {
          sessionStorage.removeItem(SS_MEDIA);
          sessionStorage.removeItem(SS_JOB);
          return false;
        })
        .then((restored) => {
          if (!restored) return;
          const restores: Promise<void>[] = [];
          if (jobId) {
            restores.push(
              getJob(jobId)
                .then((j) => {
                  if (j.media_id === mediaId) {
                    dispatchJob({ type: "set-active", job: j });
                  } else {
                    sessionStorage.removeItem(SS_JOB);
                  }
                })
                .catch(() => {
                  sessionStorage.removeItem(SS_JOB);
                }),
            );
          }
          // A job that finished while the tab was closed: adopt the
          // newest job *for this media* so the dock shows its result.
          void Promise.allSettled(restores).then(() =>
            getJobs()
              .then(({ jobs }) => {
                const newest = pickHydrationJob(jobs, mediaId);
                if (newest) {
                  dispatchJob({ type: "hydrate-latest", job: newest });
                }
              })
              .catch(() => {}),
          );
        });
    }

    try {
      chan.current = new BroadcastChannel(CH);
      chan.current.onmessage = (e) => {
        const d = e.data as {
          type?: string;
          outputDir?: string | null;
          jobId?: string;
        };
        if (d?.type === "settings") {
          dispatchSettings({
            type: "settings",
            outputDir: d.outputDir ?? null,
          });
        } else if (d?.type === "job" && d.jobId) {
          getJob(d.jobId)
            .then((j: Job) => dispatchJob({ type: "set-active", job: j }))
            .catch(() => {});
        }
      };
    } catch {
      /* BroadcastChannel unsupported → single-tab app anyway */
    }
    return () => chan.current?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // persist language + device choice
  useEffect(() => {
    if (!booted.current) return;
    localStorage.setItem(LS_LANG, ui.lang);
  }, [ui.lang]);
  useEffect(() => {
    if (!booted.current) return;
    localStorage.setItem(LS_DEVICE, ui.device);
  }, [ui.device]);

  // session persistence for refresh-resume
  useEffect(() => {
    if (media.mediaId) sessionStorage.setItem(SS_MEDIA, media.mediaId);
    else sessionStorage.removeItem(SS_MEDIA);
  }, [media.mediaId]);
  useEffect(() => {
    if (job.activeId) sessionStorage.setItem(SS_JOB, job.activeId);
    else sessionStorage.removeItem(SS_JOB);
  }, [job.activeId]);

  // notify other tabs when this tab creates a job
  const prevJobId = useRef<string | null>(null);
  useEffect(() => {
    if (
      job.activeId &&
      job.activeId !== prevJobId.current &&
      job.job?.state !== "completed"
    ) {
      try {
        chan.current?.postMessage({ type: "job", jobId: job.activeId });
      } catch {
        /* no channel */
      }
    }
    prevJobId.current = job.activeId;
  }, [job.activeId, job.job?.state]);
}

export function broadcastSettings(outputDir: string | null): void {
  try {
    new BroadcastChannel(CH).postMessage({ type: "settings", outputDir });
  } catch {
    /* unsupported */
  }
}
