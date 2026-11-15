import { useStore } from "../state/store";
import { useT } from "../i18n";

/** Numeric preview region (M9a — no waveform handles yet). Promoted to a
 *  card with the "Try a short part first" hint when silence-remove is on
 *  or the file runs past five minutes. */
export function PreviewBar({
  duration,
  prominent,
  running,
  onSubmit,
}: {
  duration: number | null;
  prominent: boolean;
  running: boolean;
  onSubmit: (start: number, end: number) => void;
}) {
  const t = useT();
  const { job, dispatchJob } = useStore();
  const { start_s, end_s } = job.previewRegion;

  const set = (patch: Partial<{ start_s: number; end_s: number }>) =>
    dispatchJob({
      type: "preview-region",
      region: { start_s, end_s, ...patch },
    });

  const bad = end_s - start_s < 1 || end_s - start_s > 60 || start_s < 0;

  const fields = (
    <div className="preview-fields">
      <label className="pf">
        <span>{t.d.preview.start}</span>
        <input
          type="number"
          className="num-field"
          dir="ltr"
          min={0}
          step={1}
          value={start_s}
          onChange={(e) => set({ start_s: Number(e.target.value) })}
          aria-label={t.d.preview.start}
        />
        <span className="unit">{t.d.preview.unit}</span>
      </label>
      <label className="pf">
        <span>{t.d.preview.end}</span>
        <input
          type="number"
          className="num-field"
          dir="ltr"
          min={0}
          max={duration ?? undefined}
          step={1}
          value={end_s}
          onChange={(e) => set({ end_s: Number(e.target.value) })}
          aria-label={t.d.preview.end}
        />
        <span className="unit">{t.d.preview.unit}</span>
      </label>
      <button
        type="button"
        className="btn-secondary"
        disabled={bad || running}
        onClick={() => onSubmit(start_s, end_s)}
      >
        {running ? t.d.preview.running : t.d.action.previewShort}
      </button>
      {bad && <span className="field-err">{t.d.preview.length}</span>}
    </div>
  );

  if (!prominent) return <div className="preview-bar">{fields}</div>;
  return (
    <div className="preview-bar prominent">
      <div className="preview-hint">{t.d.preview.hint}</div>
      {fields}
    </div>
  );
}
