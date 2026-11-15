import { forwardRef, useEffect, useMemo, useState } from "react";
import { submitJob } from "../actions";
import { getJob, jobFileUrl } from "../api/client";
import { useT } from "../i18n";
import { useTimelineMap } from "../hooks/useTimelineMap";
import { fmtSpan } from "../lib/format";
import { removedSpans } from "../lib/timeline";
import { isTerminal } from "../state/reducers";
import { useStore } from "../state/store";
import {
  AudioComparePlayer,
  type TransportHandle,
} from "./AudioComparePlayer";
import { DropZone } from "./DropZone";
import { MediaInspector } from "./MediaInspector";
import { PreviewBar } from "./PreviewBar";
import { ResultsPanel } from "./ResultsPanel";
import { useAnnounce } from "./LiveAnnouncer";

/** Player column: drop zone → inspector → A/B transport → preview bar →
 *  removed-parts list. Empty state explains the app and the first action. */
export const PlayerPanel = forwardRef<TransportHandle>(function PlayerPanel(
  _props,
  transportRef,
) {
  const t = useT();
  const store = useStore();
  const { media, selection, job } = store;
  const announce = useAnnounce();
  const [previewing, setPreviewing] = useState(false);
  const [compareFile, setCompareFile] = useState<string | null>(null);

  const timeline = useTimelineMap(job.compareJobId);

  // Resolve the compare job's primary output file.
  useEffect(() => {
    const cid = job.compareJobId;
    if (!cid) {
      setCompareFile(null);
      return;
    }
    let alive = true;
    const pick = (files?: { name: string; kind: string }[]) => {
      const audio = files?.find((f) => f.kind === "audio" || f.kind === "video");
      return audio?.name ?? files?.[0]?.name ?? null;
    };
    if (job.job?.id === cid && job.job.files?.length) {
      setCompareFile(pick(job.job.files));
    } else {
      getJob(cid)
        .then((j) => {
          if (alive) setCompareFile(pick(j.files));
        })
        .catch(() => {
          if (alive) setCompareFile(null);
        });
    }
    return () => {
      alive = false;
    };
  }, [job.compareJobId, job.job]);

  const duration = media.probe?.duration_s ?? null;

  // Default preview region tracks the file: clamp to real duration.
  const mediaId = media.mediaId;
  useEffect(() => {
    if (!mediaId || duration === null) return;
    const end = Math.max(1, Math.floor(Math.min(20, duration)));
    store.dispatchJob({
      type: "preview-region",
      region: { start_s: 0, end_s: end },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mediaId, duration]);
  const silenceRemove =
    selection.effective.some(
      (o) => o.id === "silence" && (o.params.action ?? "remove") === "remove",
    ) || ("silence" in selection.ops && !selection.disabled["silence"]);
  const prominentPreview =
    silenceRemove || (duration !== null && duration > 300);

  const running = job.job !== null && !isTerminal(job.job);
  const runningPreview = running && job.job?.kind === "preview";

  const cuts = useMemo(() => (timeline ? removedSpans(timeline) : []), [timeline]);

  if (media.status === "empty" || media.status === "uploading" || media.status === "preparing") {
    return (
      <section className="player">
        <DropZone />
      </section>
    );
  }

  if (media.status === "error") {
    return (
      <section className="player">
        <div className="drop busy" role="alert">
          <div className="drop-filename">
            <bdi>{media.filename}</bdi>
          </div>
          <div className="drop-meta neg">
            {t.msg(media.error?.code, media.error?.params, media.error?.message)}
          </div>
          <DropZone />
        </div>
      </section>
    );
  }

  return (
    <section className="player">
      <MediaInspector />
      {media.mediaId && (
        <AudioComparePlayer
          ref={transportRef}
          mediaId={media.mediaId}
          processedUrl={
            job.compareJobId && compareFile
              ? jobFileUrl(job.compareJobId, compareFile)
              : null
          }
          timeline={timeline}
          onAnnounce={announce}
        />
      )}
      <PreviewBar
        duration={duration}
        prominent={prominentPreview}
        running={runningPreview}
        onSubmit={(s, e) => {
          setPreviewing(true);
          void submitJob(store, { preview: { start_s: s, end_s: e } }).finally(
            () => setPreviewing(false),
          );
        }}
      />
      <ResultsPanel />
      {cuts.length > 0 && (
        <div className="cut-list">
          <div className="pop-title">{t.d.cutList.title}</div>
          <ul>
            {cuts.map(([a, b], i) => (
              <li key={i} className="mono" dir="ltr">
                {t.t(t.d.cutList.removed, { t: fmtSpan(b - a) })} —{" "}
                {fmtSpan(a)} → {fmtSpan(b)}
              </li>
            ))}
          </ul>
        </div>
      )}
      {previewing && <span className="sr-only">{t.d.preview.running}</span>}
    </section>
  );
});
