import { useEffect, useRef, useState } from "react";
import {
  cancelModelDownload,
  startModelDownload,
  watchModelDownload,
} from "../api/client";
import type { DisabledOp, ModelDownloadState, OpSpec } from "../api/types";
import { useT } from "../i18n";
import { notifyModelsChanged } from "../hooks/useOperations";
import { useStore } from "../state/store";
import { ParamControl } from "./ParamControl";
import { Switch } from "./Switch";

function reasonText(t: ReturnType<typeof useT>, d: DisabledOp): string {
  const loc = t.d.conflict as Record<string, Record<string, string> | string>;
  const code = d.code;
  const group = loc[code];
  if (typeof group === "object" && group) {
    if (d.blocker_op_id && d.blocker_op_id in group) {
      return group[d.blocker_op_id] as string;
    }
    if ("other" in group) return group.other as string;
  }
  return d.message || code;
}

function pillText(t: ReturnType<typeof useT>, op: OpSpec): string | null {
  switch (op.unavailable_code) {
    case "planned":
      return t.d.pill.comingLater;
    case "model_missing":
      return t.d.pill.modelMissing;
    case "module_missing":
    default:
      return op.available ? null : t.d.pill.unavailable;
  }
}

/** M10 T3: "Download model" on an unavailable card — POSTs the download
 *  endpoint, watches SSE progress, offers Cancel. On done it broadcasts
 *  ss-models-changed so the rack refetches and the card unlocks. */
function ModelDownloadButton({
  modelId,
  mb,
}: {
  modelId: string;
  mb: number | null;
}) {
  const t = useT();
  const [st, setSt] = useState<ModelDownloadState | null>(null);
  const watcher = useRef<ReturnType<typeof watchModelDownload> | null>(
    null,
  );

  useEffect(() => () => watcher.current?.close(), []);

  const begin = async () => {
    setSt({ model_id: modelId, state: "downloading" });
    try {
      const s = await startModelDownload(modelId);
      setSt(s);
      if (s.state === "done") {
        notifyModelsChanged();
        return;
      }
      watcher.current = watchModelDownload(modelId, setSt);
      watcher.current.promise
        .then((final) => {
          setSt(final);
          if (final.state === "done") notifyModelsChanged();
        })
        .catch(() => setSt({ model_id: modelId, state: "error" }));
    } catch {
      setSt({ model_id: modelId, state: "error" });
    }
  };

  if (st?.state === "downloading") {
    const pct = st.pct != null ? Math.round(st.pct) : null;
    return (
      <div className="model-dl" role="status">
        <div className="model-dl-bar">
          <div
            className="model-dl-fill"
            style={{ width: pct != null ? `${pct}%` : "10%" }}
          />
        </div>
        <span className="model-dl-label">
          {pct != null
            ? t.t(t.d.models.downloading, { pct })
            : t.d.models.downloadingWait}
        </span>
        <button
          type="button"
          className="link-btn"
          onClick={() => void cancelModelDownload(modelId)}
        >
          {t.d.models.cancel}
        </button>
      </div>
    );
  }
  if (st?.state === "error") {
    return (
      <div className="model-dl">
        <span className="model-dl-label err">{t.d.models.failed}</span>
        <button
          type="button"
          className="link-btn"
          onClick={() => void begin()}
        >
          {t.d.models.retry}
        </button>
      </div>
    );
  }
  if (st?.state === "done") {
    return <div className="model-dl done">{t.d.models.done}</div>;
  }
  return (
    <button type="button" className="link-btn model-dl-btn" onClick={() => void begin()}>
      {t.t(t.d.models.download, { mb: mb ?? "?" })}
    </button>
  );
}

/** One rack card: switch, name, description, params (advanced under a
 *  disclosure), reset, conflict pill. Disabled/unavailable cards show
 *  why — never silently dead (§6). */
export function OpCard({
  op,
  conflict,
}: {
  op: OpSpec;
  conflict: DisabledOp | null;
}) {
  const t = useT();
  const { media, selection, dispatchSelection } = useStore();
  const [advOpen, setAdvOpen] = useState(false);

  const enabled = op.id in selection.ops;
  const params = selection.ops[op.id] ?? {};
  const opText = t.d.op[op.id as keyof typeof t.d.op];
  const name = opText?.name ?? op.name;
  const desc = opText?.desc ?? op.description;
  const pill = pillText(t, op);
  const conflicted = enabled && conflict !== null;

  const basic = op.params.filter((p) => !p.advanced);
  const adv = op.params.filter((p) => p.advanced);

  return (
    <article
      className={`op-card${enabled && !conflicted ? " on" : ""}${conflicted ? " conflict" : ""}${!op.available ? " unavailable" : ""}`}
      data-op-id={op.id}
    >
      <div className="op-head">
        <div className="op-titles">
          <div className="op-name">{name}</div>
          <div className="op-desc">{desc}</div>
        </div>
        <Switch
          checked={enabled}
          onChange={() => dispatchSelection({ type: "toggle", opId: op.id })}
          disabled={!op.available || media.status !== "ready"}
          label={name}
        />
      </div>

      {pill && <div className="pill">{pill}</div>}
      {!op.available && op.unavailable_model && (
        <ModelDownloadButton
          modelId={op.unavailable_model}
          mb={op.unavailable_model_mb}
        />
      )}
      {conflicted && (
        <div className="pill conflict-pill">{reasonText(t, conflict)}</div>
      )}

      {enabled && !conflicted && basic.length + adv.length > 0 && (
        <div className="op-params">
          {basic.map((p) => (
            <ParamControl
              key={p.key}
              opId={op.id}
              spec={p}
              value={params[p.key]}
              onChange={(v) =>
                dispatchSelection({ type: "set-param", opId: op.id, key: p.key, value: v })
              }
            />
          ))}
          {adv.length > 0 && (
            <>
              <button
                type="button"
                className="link-btn adv-toggle"
                aria-expanded={advOpen}
                onClick={() => setAdvOpen((v) => !v)}
              >
                {t.d.rack.advanced}
              </button>
              {advOpen &&
                adv.map((p) => (
                  <ParamControl
                    key={p.key}
                    opId={op.id}
                    spec={p}
                    value={params[p.key]}
                    onChange={(v) =>
                      dispatchSelection({ type: "set-param", opId: op.id, key: p.key, value: v })
                    }
                  />
                ))}
            </>
          )}
          <button
            type="button"
            className="link-btn op-reset"
            onClick={() =>
              dispatchSelection({ type: "reset-params", opId: op.id })
            }
          >
            {t.d.rack.reset}
          </button>
        </div>
      )}
    </article>
  );
}
