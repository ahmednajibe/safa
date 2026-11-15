import { useState } from "react";
import { saveOne } from "../actions";
import { deleteJob } from "../api/client";
import type { Job } from "../api/types";
import { useT } from "../i18n";
import { fmtBytes } from "../lib/format";
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
                store.dispatchJob({ type: "set-active", job: j });
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
                <span
                  className={`state state-${j.state.replace(/[^a-z]/g, "")}`}
                >
                  {stateLabel(t, j.state)}
                </span>
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
                j.files?.map((f) => (
                  <button
                    key={f.name}
                    type="button"
                    className="link-btn"
                    onClick={() => void saveOne(store, j.id, f)}
                  >
                    {t.d.history.saveAgain} <bdi>{f.name}</bdi>{" "}
                    {fmtBytes(f.size_bytes)}
                  </button>
                ))}
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
      {job.saving && (
        <div className="drawer-foot" role="status">
          {t.t(t.d.save.saving, {
            pct: t.num(Math.round(job.saving.pct * 100)),
          })}
        </div>
      )}
    </aside>
  );
}
