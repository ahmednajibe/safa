import { fmtDuration } from "../lib/format";
import { rulerTicks } from "../lib/peaks";

/** Ruler strip over the waveform well (§6): 12px faint tabular labels in
 *  the source-time domain, tracking the original wave's scroll/zoom. It
 *  lives inside the dir="ltr" island — tick positions are pure px. */
export function TimeRuler({
  duration,
  pxPerSec,
  scrollX,
}: {
  duration: number;
  pxPerSec: number;
  scrollX: number;
}) {
  if (!(duration > 0) || !(pxPerSec > 0)) {
    return <div className="ruler" aria-hidden="true" />;
  }
  const ticks = rulerTicks(duration, pxPerSec);
  return (
    <div className="ruler" aria-hidden="true">
      <div
        className="ruler-track"
        style={{ transform: `translateX(${-scrollX}px)` }}
      >
        {ticks.map((t, i) => (
          <span
            key={`${t}-${i}`}
            className="ruler-tick mono"
            style={{ left: `${t * pxPerSec}px` }}
          >
            {fmtDuration(t)}
          </span>
        ))}
      </div>
    </div>
  );
}
