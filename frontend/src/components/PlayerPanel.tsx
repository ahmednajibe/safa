import { forwardRef, useEffect, useMemo, useState } from "react";
import { submitJob } from "../actions";
import { getJob, jobFileUrl } from "../api/client";
import type { Job } from "../api/types";
import { useT } from "../i18n";
import { useTimelineMap } from "../hooks/useTimelineMap";
import { fmtSpan } from "../lib/format";
import { hatchSpans } from "../lib/peaks";
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
  const [compareJob, setCompareJob] = useState<Job | null>(null);

  const timeline = useTimelineMap(job.compareJobId);

  // Resolve the compare job: its audio output, an optional processed
  // video file, and (for preview jobs) the region start the timeline is
  // offset by.
  useEffect(() => {
    const cid = job.compareJobId;
    if (!cid) {
      setCompareJob(null);
      return;
    }
    let alive = true;
    if (job.job?.id === cid && job.job.files?.length) {
      setCompareJob(job.job);
    } else {
      getJob(cid)
        .then((j) => {
          if (alive) setCompareJob(j);
        })
        .catch(() => {
          if (alive) setCompareJob(null);
        });
    }
    return () => {
      alive = false;
    };
  }, [job.compareJobId, job.job]);

  const compareFile = useMemo(() => {
    const files = compareJob?.files;
    const audio = files?.find((f) => f.kind === "audio" || f.kind === "video");
    return audio?.name ?? files?.[0]?.name ?? null;
  }, [compareJob]);
  const compareVideoFile = useMemo(
    () => compareJob?.files?.find((f) => f.kind === "video")?.name ?? null,
    [compareJob],
  );
  const compareSrcOffset = compareJob?.preview?.start_s ?? 0;

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

  const cuts = useMemo(
    () => (timeline ? hatchSpans(timeline, compareSrcOffset) : []),
    [timeline, compareSrcOffset],
  );

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
          srcDuration={duration ?? 0}
          hasVideo={media.probe?.has_video === true}
          compareJobId={job.compareJobId}
          processedUrl={
            job.compareJobId && compareFile
              ? jobFileUrl(job.compareJobId, compareFile)
              : null
          }
          processedVideoUrl={
            job.compareJobId && compareVideoFile
              ? jobFileUrl(job.compareJobId, compareVideoFile)
              : null
          }
          timeline={timeline}
          srcOffset={compareSrcOffset}
          region={job.previewRegion}
          onRegion={(r) =>
            store.dispatchJob({ type: "preview-region", region: r })
          }
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
