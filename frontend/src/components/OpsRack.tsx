import type { OpSpec } from "../api/types";
import { useT } from "../i18n";
import { useStore } from "../state/store";
import { OpCard } from "./OpCard";

/** The operation rack: every operation in fixed backend order, spine at
 *  logical inline-start (right in RTL — §12.1). */
export function OpsRack({ ops, loaded }: { ops: OpSpec[]; loaded: boolean }) {
  const t = useT();
  const { media, selection } = useStore();
  const onCount = Object.keys(selection.ops).length;

  return (
    <aside className="rack" aria-label={t.d.rack.steps}>
      <div className="rack-head">
        <span className="rack-title">{t.d.rack.header}</span>
        <span className="rack-count">
          {loaded
            ? t.t(t.d.rack.nOn, { on: t.num(onCount), total: t.num(ops.length) })
            : "…"}
        </span>
      </div>
      {media.status !== "ready" && (
        <div className="rack-empty">{t.d.rack.needFile}</div>
      )}
      <div className="rack-list">
        {ops.map((op) => (
          <OpCard
            key={op.id}
            op={op}
            conflict={selection.disabled[op.id] ?? null}
          />
        ))}
      </div>
    </aside>
  );
}
