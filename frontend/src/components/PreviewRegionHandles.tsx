import { useRef } from "react";
import { useT } from "../i18n";
import { fmtTimecode } from "../lib/format";
import {
  dragRegion,
  type DragPart,
  type Region,
} from "../lib/region";

/** Preview region on the source waveform: white 1px edges + --preview-fill,
 *  draggable handles and a draggable body — two-way synced with the numeric
 *  start/end fields in PreviewBar (both write job.previewRegion). The track
 *  slides with the original wave's scroll; positions are absolute content
 *  px, so converting a pointer to seconds is just distance over px/sec. */
export function PreviewRegionHandles({
  region,
  duration,
  pxPerSec,
  scrollX,
  onChange,
}: {
  region: Region;
  duration: number;
  pxPerSec: number;
  scrollX: number;
  onChange: (r: Region) => void;
}) {
  const t = useT();
  const trackRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{
    part: DragPart;
    grabT: number;
    orig: Region;
  } | null>(null);

  const toTime = (clientX: number): number => {
    const el = trackRef.current;
    if (!el || !(pxPerSec > 0)) return 0;
    return (clientX - el.getBoundingClientRect().left) / pxPerSec;
  };

  const onDown = (part: DragPart) => (e: React.PointerEvent<HTMLElement>) => {
    e.preventDefault();
    // A handle's pointerdown must not reach .preview-region — it would
    // overwrite drag.part with "body" and turn every resize into a move.
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { part, grabT: toTime(e.clientX), orig: { ...region } };
  };
  const onMove = (e: React.PointerEvent<HTMLElement>) => {
    const d = drag.current;
    if (!d) return;
    const time = toTime(e.clientX);
    const next =
      d.part === "body"
        ? dragRegion(d.orig, "body", time - d.grabT, duration)
        : dragRegion(d.orig, d.part, time, duration);
    if (next.start_s !== region.start_s || next.end_s !== region.end_s) {
      onChange(next);
    }
  };
  const onUp = (e: React.PointerEvent<HTMLElement>) => {
    if (drag.current) {
      drag.current = null;
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
  };

  /** Arrow-key nudge on a focused handle: ±0.5 s that edge. preventDefault
   *  keeps the global seek shortcut out of it (it skips prevented keys). */
  const keyNudge = (part: "start" | "end") => (e: React.KeyboardEvent) => {
    let d = 0;
    if (e.key === "ArrowLeft") d = -0.5;
    else if (e.key === "ArrowRight") d = 0.5;
    else return;
    e.preventDefault();
    const t = (part === "start" ? region.start_s : region.end_s) + d;
    onChange(dragRegion(region, part, t, duration));
  };

  const w = Math.max(0, region.end_s - region.start_s);
  const handlers = { onPointerMove: onMove, onPointerUp: onUp };

  return (
    <div
      ref={trackRef}
      className="preview-track"
      style={{ transform: `translateX(${-scrollX}px)` }}
    >
      <div
        className="preview-region"
        role="slider"
        tabIndex={0}
        aria-label={t.d.well.region}
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(region.start_s)}
        aria-valuetext={`${fmtTimecode(region.start_s)} – ${fmtTimecode(region.end_s)}`}
        style={{
          left: `${region.start_s * pxPerSec}px`,
          width: `${w * pxPerSec}px`,
        }}
        onPointerDown={onDown("body")}
        {...handlers}
      >
        <div
          className="ph ph-start"
          role="slider"
          tabIndex={0}
          aria-label={t.d.well.regionStart}
          aria-valuemin={0}
          aria-valuemax={Math.round(duration)}
          aria-valuenow={Math.round(region.start_s)}
          aria-valuetext={fmtTimecode(region.start_s)}
          onPointerDown={onDown("start")}
          onKeyDown={keyNudge("start")}
          {...handlers}
        />
        <div
          className="ph ph-end"
          role="slider"
          tabIndex={0}
          aria-label={t.d.well.regionEnd}
          aria-valuemin={0}
          aria-valuemax={Math.round(duration)}
          aria-valuenow={Math.round(region.end_s)}
          aria-valuetext={fmtTimecode(region.end_s)}
          onPointerDown={onDown("end")}
          onKeyDown={keyNudge("end")}
          {...handlers}
        />
      </div>
    </div>
  );
}
