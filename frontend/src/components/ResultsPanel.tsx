import type { JobStage } from "../api/types";
import { useT } from "../i18n";
import { useStore } from "../state/store";

/** Job-level save notices live in the dock's done/save area, never under a
 *  stage row (a "Saved to …" line under the last stage reads as that
 *  stage's result). */
const SAVE_MSG_CODES = new Set(["saved_to", "auto_save_failed"]);

/** Results panel (below the preview bar, after a job): every stage in run
 *  order — translated name, the device it actually used, wall seconds and
 *  its translated summary — then the job's warnings. Nothing before a job. */
export function ResultsPanel() {
  const t = useT();
  const { job } = useStore();
  const j = job.job;
  if (!j || j.state !== "completed" || !j.stages?.length) return null;

  const stageDevice = (s: JobStage): string | null =>
    s.actual_device ?? s.device ?? null;

  return (
    <section className="results" aria-label={t.d.results.title}>
      <div className="pop-title">{t.d.results.title}</div>
      <ul className="results-list">
        {j.stages.map((s, i) => {
          const opText = t.d.op[s.id as keyof typeof t.d.op];
          const name = opText?.name ?? s.name;
          const device = stageDevice(s);
          const summary = s.message_code
            ? t.msg(s.message_code, s.message_params)
            : "";
          return (
            <li key={s.id || i} className="result-row">
              <div className="result-head">
                <span className="result-name">{name}</span>
                {device && (
                  <span className="pill">
                    {device === "gpu" ? t.d.pill.gpu : t.d.pill.cpu}
                  </span>
                )}
                {typeof s.seconds === "number" && (
                  <span className="result-secs mono" dir="ltr">
                    {t.num(s.seconds, { maximumFractionDigits: 1 })} s
                  </span>
                )}
              </div>
              {s.state && s.state !== "done" && (
                <div className="result-msg">
                  {(t.d.history.state as Record<string, string>)[s.state] ??
                    s.state}
                </div>
              )}
              {summary && <div className="result-msg">{summary}</div>}
              {s.warning_code && (
                <div className="result-msg warn">
                  {t.msg(s.warning_code, s.warning_params)}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {j.warnings
        ?.filter((w) => !SAVE_MSG_CODES.has(w.warning_code))
        .map((w, i) => (
          <div key={i} className="result-msg warn">
            {t.msg(w.warning_code, w.warning_params)}
          </div>
        ))}
    </section>
  );
}
