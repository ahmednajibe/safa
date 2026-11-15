import {
  cancelActiveJob,
  retryFailedJob,
  saveAll,
  saveOne,
} from "../actions";
import { revealSaved } from "../api/client";
import type { Estimate, JobFile } from "../api/types";
import { useT } from "../i18n";
import { fmtEta, truncateMiddle } from "../lib/format";
import { isTerminal, type MediaStatus } from "../state/reducers";
import { useStore } from "../state/store";
import { SegmentedControl } from "./SegmentedControl";
import { Switch } from "./Switch";

const FORMATS = ["wav", "flac", "mp3", "aac"] as const;

function estimateLine(
  t: ReturnType<typeof useT>,
  est: Estimate | null,
  device: "gpu" | "cpu",
  gpuName: string | null,
): string {
  if (!est) return t.d.progress.estimating;
  const bucket = t.t(
    t.d.estimate.bucket[est.bucket_code] ?? est.bucket,
    { minutes: est.minutes ?? 0 },
  );
  return device === "gpu"
    ? t.t(t.d.estimate.gpuSub, { gpu: gpuName ?? "GPU", bucket })
    : t.t(t.d.estimate.cpuSub, { bucket });
}

/** The always-visible dock subline (§6): estimate → no-steps →
 *  "Add a file to start" when there's no media at all. */
export function dockSubline(
  t: ReturnType<typeof useT>,
  opts: {
    mediaReady: boolean;
    hasOps: boolean;
    mediaStatus: MediaStatus;
    estimate: Estimate | null;
    device: "gpu" | "cpu";
    gpuName: string | null;
  },
): string {
  if (opts.mediaReady) {
    return opts.hasOps
      ? estimateLine(t, opts.estimate, opts.device, opts.gpuName)
      : t.d.misc.noSteps;
  }
  return opts.mediaStatus === "empty" || opts.mediaStatus === "error"
    ? t.d.rack.needFile
    : t.d.misc.waitingFile;
}

function fileKindLabel(
  t: ReturnType<typeof useT>,
  f: JobFile,
): string {
  const k = t.d.fileKind as Record<string, string>;
  return k[f.kind] ?? f.name;
}

/** Save cluster for a completed job (§13): per file one action — Save /
 *  Save a copy (auto-saved) — followed by a neutral "Saved" pill and, for
 *  auto-saved files, "Show in folder". The saved path is middle-truncated
 *  in a <bdi dir="ltr"> so it never mirrors or overflows. */
function SaveCluster({ jobId, files }: { jobId: string; files: JobFile[] }) {
  const t = useT();
  const store = useStore();
  const { job } = store;

  const savedAnywhere = files.find((f) => f.saved_path)?.saved_path;

  return (
    <div className="save-cluster">
      {files.map((f) => {
        const savingThis = job.saving?.name === f.name;
        const busy = !!job.saving || !!job.savingAll;
        return (
          <span key={f.name} className="save-item">
            {savingThis ? (
              <button type="button" className="btn-primary" disabled>
                {t.t(t.d.save.saving, {
                  pct: t.num(Math.round((job.saving?.pct ?? 0) * 100)),
                })}
              </button>
            ) : f.saved_path ? (
              <button
                type="button"
                className="btn-secondary"
                disabled={busy}
                onClick={() => void saveOne(store, jobId, f)}
              >
                {t.d.save.copy}
              </button>
            ) : !job.saved[f.name] ? (
              <button
                type="button"
                className="btn-primary"
                disabled={busy}
                onClick={() => void saveOne(store, jobId, f)}
              >
                {t.t(t.d.save.as, { kind: fileKindLabel(t, f) })}
              </button>
            ) : null}
            {!savingThis && (job.saved[f.name] || f.saved_path) && (
              <span className="pill">{t.d.save.saved}</span>
            )}
            {f.saved_path && (
              <button
                type="button"
                className="link-btn"
                onClick={() => revealSaved(jobId, f.name).catch(() => {})}
              >
                {t.d.settings.showInFolder}
              </button>
            )}
          </span>
        );
      })}
      {files.length > 1 && (
        <button
          type="button"
          className="btn-secondary"
          disabled={!!job.saving || !!job.savingAll}
          onClick={() => void saveAll(store, jobId, files)}
        >
          {job.savingAll
            ? t.t(t.d.save.savingAll, {
                n: t.num(job.savingAll.index + 1),
                total: t.num(job.savingAll.total),
                name: job.savingAll.name,
              })
            : t.d.save.all}
        </button>
      )}
      {savedAnywhere && (
        <span className="save-note">
          {t.t(t.d.save.savedTo, { folder: "" })}
          <bdi dir="ltr">{truncateMiddle(savedAnywhere, 46)}</bdi>
        </span>
      )}
      {job.saveError && (
        <span className="save-note neg" role="alert">
          {t.msg(job.saveError.code, job.saveError.params, t.d.save.error)}
        </span>
      )}
    </div>
  );
}

/** Terminal job block: Done · N files + the save cluster, or the
 *  failed/cancelled/interrupted message + Retry. Rendered inside the dock
 *  on wide screens and in the content foot on narrow ones. */
export function JobResult() {
  const t = useT();
  const store = useStore();
  const j = store.job.job;
  if (!j || !isTerminal(j)) return null;
  return (
    <div className="job-result">
      {j.state === "completed" && (
        <>
          <div className="dock-msg">
            {j.kind === "preview"
              ? t.d.progress.previewDone
              : j.files?.length
                ? t.msg("done_files", { count: j.files.length })
                : t.d.progress.done}
          </div>
          {j.files && j.files.length > 0 && (
            <SaveCluster jobId={j.id} files={j.files} />
          )}
        </>
      )}
      {j.state === "failed" && (
        <div className="dock-msg neg" role="alert">
          {t.msg(j.error_code, j.error_params, j.error ?? t.d.progress.failed)}
        </div>
      )}
      {j.state === "cancelled" && (
        <div className="dock-msg">{t.d.progress.cancelled}</div>
      )}
      {j.state === "interrupted" && (
        <div className="dock-msg">
          {t.msg("job_interrupted", {}, j.message)}
        </div>
      )}
      {(j.state === "failed" ||
        j.state === "cancelled" ||
        j.state === "interrupted") && (
        <button
          type="button"
          className="btn-secondary"
          onClick={() => void retryFailedJob(store)}
        >
          {t.d.action.retry}
        </button>
      )}
    </div>
  );
}

/** The bottom dock: output settings + estimate + Preview/Process, then the
 *  running stepper and the completed/failed states (§8–§10, §12–§13).
 *  On narrow screens (`compact`) it collapses to a single row — format
 *  select, device toggle, Process — while keep-video, the subline, and the
 *  save actions move into the content foot above it. */
export function Dock({
  estimate,
  gpuName,
  mediaReady,
  hasOps,
  compact,
  onPreview,
  onProcess,
}: {
  estimate: Estimate | null;
  gpuName: string | null;
  mediaReady: boolean;
  hasOps: boolean;
  compact: boolean;
  onPreview: () => void;
  onProcess: () => void;
}) {
  const t = useT();
  const store = useStore();
  const { ui, job, media } = store;

  const running = job.job !== null && !isTerminal(job.job);
  const j = job.job;
  const etaText =
    j?.eta_sec != null && fmtEta(j.eta_sec)
      ? t.t(t.d.progress.eta, { t: fmtEta(j.eta_sec) })
      : j?.eta_sec != null
        ? t.d.progress.etaShort
        : null;

  const stageName = (() => {
    if (!j) return "";
    const sid = j.stage_id;
    const local = (t.d.stage as Record<string, string>)[sid ?? ""] ?? null;
    return local ?? j.stage ?? t.d.progress.working;
  })();

  const canSubmit = mediaReady && hasOps && !running;
  const isVideo = media.probe?.has_video === true;

  return (
    <footer className="dock" aria-label="Actions">
      {/* ── idle: controls ─────────────────────────── */}
      {(!j || isTerminal(j)) && (
        <div className="dock-idle">
          <div className="dock-opts">
            {compact ? (
              <select
                className="select"
                aria-label={t.d.output.format}
                value={ui.format}
                onChange={(e) =>
                  store.dispatchUi({
                    type: "output",
                    format: e.target.value as typeof ui.format,
                  })
                }
              >
                {FORMATS.map((f) => (
                  <option key={f} value={f}>
                    {t.d.output.formats[f]}
                  </option>
                ))}
              </select>
            ) : (
              <SegmentedControl
                label={t.d.output.format}
                small
                options={FORMATS.map((f) => ({
                  value: f,
                  label: t.d.output.formats[f],
                }))}
                value={ui.format}
                onChange={(v) =>
                  store.dispatchUi({
                    type: "output",
                    format: v as typeof ui.format,
                  })
                }
              />
            )}
            {!compact && isVideo && (
              <label className="kv-row">
                <Switch
                  checked={ui.keepVideo}
                  onChange={(v) =>
                    store.dispatchUi({ type: "output", keepVideo: v })
                  }
                  label={t.d.output.keepVideo}
                />
                <span>{t.d.output.keepVideo}</span>
              </label>
            )}
            <SegmentedControl
              label={t.d.device.label}
              small
              options={[
                { value: "gpu", label: t.d.device.gpu },
                { value: "cpu", label: t.d.device.cpu },
              ]}
              value={ui.device}
              onChange={(v) =>
                store.dispatchUi({ type: "device", device: v as "gpu" | "cpu" })
              }
            />
            {compact ? (
              <>
                <span className="ms-auto" />
                <button
                  type="button"
                  className="btn-primary"
                  disabled={!canSubmit}
                  onClick={onProcess}
                >
                  {t.d.action.process}
                </button>
              </>
            ) : (
              <span className="estimate-sub">
                {dockSubline(t, {
                  mediaReady,
                  hasOps,
                  mediaStatus: media.status,
                  estimate,
                  device: ui.device,
                  gpuName,
                })}
              </span>
            )}
          </div>
          {!compact && (
            <div className="dock-actions">
              <button
                type="button"
                className="btn-secondary"
                disabled={!canSubmit}
                onClick={onPreview}
              >
                {t.d.action.preview}
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={!canSubmit}
                onClick={onProcess}
              >
                {t.d.action.process}
              </button>
            </div>
          )}
          {!compact && <JobResult />}
        </div>
      )}

      {/* ── running: stepper ───────────────────────── */}
      {running && j && (
        <div className="dock-running">
          <div className="run-row">
            <span className="run-stage">
              {j.queue_position != null && j.state === "queued"
                ? t.d.progress.queued
                : stageName}
            </span>
            <span className="run-meta mono">
              {j.stage_idx != null && j.total_stages != null && (
                <>
                  {t.t(t.d.progress.step, {
                    n: t.num(j.stage_idx + 1),
                    total: t.num(j.total_stages),
                  })}{" "}
                  ·{" "}
                </>
              )}
              {j.overall_pct != null && `${t.num(Math.round(j.overall_pct))}%`}
              {etaText && ` · ${etaText}`}
              {j.device && ` · ${j.device.toUpperCase()}`}
            </span>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void cancelActiveJob(store)}
            >
              {t.d.action.cancel}
            </button>
          </div>
          <div
            className="progress"
            role="progressbar"
            aria-valuenow={Math.round(j.overall_pct ?? 0)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="progress-fill"
              style={{ inlineSize: `${Math.min(100, j.overall_pct ?? 0)}%` }}
            />
          </div>
          {(j.message_code || j.message) && (
            <div className="dock-msg">
              {t.msg(j.message_code, j.message_params, j.message)}
            </div>
          )}
          {j.warnings?.map((w, i) => (
            <div key={i} className="dock-msg warn">
              {t.msg(w.warning_code, w.warning_params)}
            </div>
          ))}
        </div>
      )}
    </footer>
  );
}
