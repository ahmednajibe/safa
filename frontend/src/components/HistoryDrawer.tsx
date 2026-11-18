import { useState } from "react";
import { adoptJob, saveOne } from "../actions";
import { deleteJob, setJobKeep } from "../api/client";
import type { Job } from "../api/types";
import { useT } from "../i18n";
import { fmtBytes, fmtDate } from "../lib/format";
import { saveKey } from "../state/reducers";
import { useStore } from "../state/store";

const TERMINAL_RE = /completed|failed|cancelled|interrupted/;

function stateLabel(t: ReturnType<typeof useT>, state: string): string {
  const m = t.d.history.state as Record<string, string>;
  return m[state] ?? state;
}

/** Job history drawer (slides from inline-end). Clicking a row makes it the
 *  active job — its result card fills the dock; delete asks first (§13). */
export function HistoryDrawer({
  jobs,
  refresh,
  onClose,
}: {
  jobs: Job[];
  refresh: () => void;
  onClose: () => void;
}) {
  const t = useT();
  const store = useStore();
  const { job } = store;
  const [confirmId, setConfirmId] = useState<string | null>(null);
  /** Auto-delete date for a terminal job: finished_at + retention days. */
  const expiry = (j: Job): string | null => {
    const d = store.settings.retentionDays;
    if (d === "never" || j.keep || !j.finished_at) return null;
    const ms = new Date(j.finished_at).getTime() + d * 86_400_000;
    return Number.isFinite(ms) ? new Date(ms).toISOString() : null;
  };

  return (
    <aside className="drawer" aria-label={t.d.history.title}>
      <div className="drawer-head">
        <span className="pop-title">{t.d.history.title}</span>
        <button
          type="button"
          className="icon-btn"
          aria-label={t.d.action.close}
          onClick={onClose}
        >
          ×
        </button>
      </div>
      {jobs.length === 0 && <p className="drawer-empty">{t.d.history.empty}</p>}
      <ul className="drawer-list">
        {jobs.map((j) => (
          <li key={j.id} className="drawer-item">
            <button
              type="button"
              className={`drawer-row${job.activeId === j.id ? " active" : ""}`}
              onClick={() => {
                void adoptJob(store, j);
                onClose();
              }}
            >
              <span className="drawer-name">
                <bdi>{j.filename ?? j.media_id}</bdi>
              </span>
              <span className="drawer-meta">
                {j.kind === "preview" && (
                  <span className="pill">{t.d.history.previewTag}</span>
                )}
                {j.keep && (
                  <span className="pill kept">{t.d.history.kept}</span>
                )}
                <span
                  className={`state state-${j.state.replace(/[^a-z]/g, "")}`}
                >
                  {stateLabel(t, j.state)}
                </span>
                {expiry(j) && (
                  <span className="keep-until">
                    {t.t(t.d.history.keptUntil, {
                      date: fmtDate(expiry(j) as string, t.lang),
                    })}
                  </span>
                )}
              </span>
              <span className="drawer-ops">
                {j.ops
                  .map((o) => {
                    const ot = t.d.op[o.id as keyof typeof t.d.op];
                    return ot?.name ?? o.id;
                  })
                  .join(" · ")}
              </span>
            </button>
            <div className="drawer-actions">
              {j.state === "completed" &&
                j.files?.map((f) => {
                  // job-keyed store state: the same save shows here and in
                  // the dock, and survives closing/reopening the drawer
                  const savingThis =
                    job.saving?.jobId === j.id && job.saving.name === f.name;
                  const savedThis = !!job.saved[saveKey(j.id, f.name)];
                  const errThis =
                    job.saveError?.jobId === j.id ? job.saveError : null;
                  return (
                    <span key={f.name} className="drawer-save">
                      {savingThis ? (
                        <span className="save-note" role="status">
                          {(job.saving?.pct ?? 0) < 0
                            ? t.d.save.choosing
                            : t.t(t.d.save.saving, {
                                pct: t.num(Math.round((job.saving?.pct ?? 0) * 100)),
                              })}
                        </span>
                      ) : (
                        <button
                          type="button"
                          className="link-btn"
                          disabled={!!job.saving || !!job.savingAll}
                          onClick={() => void saveOne(store, j.id, f)}
                        >
                          {t.d.history.saveAgain} <bdi>{f.name}</bdi>{" "}
                          {fmtBytes(f.size_bytes)}
                        </button>
                      )}
                      {savedThis && !savingThis && (
                        <span className="pill">{t.d.save.saved}</span>
                      )}
                      {errThis && (
                        <span className="save-note neg" role="alert">
                          {t.msg(errThis.code, errThis.params, t.d.save.error)}
                        </span>
                      )}
                    </span>
                  );
                })}
              {TERMINAL_RE.test(j.state) && (
                <label className="keep-row">
                  <input
                    type="checkbox"
                    checked={j.keep}
                    onChange={(e) =>
                      setJobKeep(j.id, e.target.checked)
                        .then(() => refresh())
                        .catch(() => refresh())
                    }
                  />
                  <span>{t.d.history.keepForever}</span>
                </label>
              )}
              {TERMINAL_RE.test(j.state) &&
                (confirmId === j.id ? (
                  <span className="confirm-row">
                    {t.d.history.confirmDelete}
                    <button
                      type="button"
                      className="link-btn neg"
                      onClick={() =>
                        deleteJob(j.id)
                          .then(() => {
                            setConfirmId(null);
                            refresh();
                            if (job.activeId === j.id) {
                              store.dispatchJob({ type: "set-active", job: null });
                            }
                          })
                          .catch(() => setConfirmId(null))
                      }
                    >
                      {t.d.action.delete}
                    </button>
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => setConfirmId(null)}
                    >
                      {t.d.action.keep}
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    className="link-btn neg"
                    onClick={() => setConfirmId(j.id)}
                  >
                    {t.d.action.delete}
                  </button>
                ))}
            </div>
          </li>
        ))}
      </ul>
    </aside>
  );
}
