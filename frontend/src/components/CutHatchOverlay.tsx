import { useT } from "../i18n";
import { fmtSpan } from "../lib/format";

/** Removed spans on the original waveform: --cut-fill + 45° hatch (a real
 *  pattern — colour is never the only signal) with a hover/focus
 *  "Removed 0:03.2" label. The whole track slides with the original
 *  wave's scroll; in Arabic the label span is dir="rtl" inside this LTR
 *  island (§12.1). */
export function CutHatchOverlay({
  spans,
  pxPerSec,
  scrollX,
}: {
  spans: [number, number][];
  pxPerSec: number;
  scrollX: number;
}) {
  const t = useT();
  if (!spans.length || !(pxPerSec > 0)) return null;
  return (
    <div
      className="cut-track"
      style={{ transform: `translateX(${-scrollX}px)` }}
    >
      {spans.map(([a, b], i) => {
        const label = t.t(t.d.cutList.removed, { t: fmtSpan(b - a) });
        return (
          <div
            key={i}
            className="cut-span"
            tabIndex={0}
            role="note"
            aria-label={label}
            style={{ left: `${a * pxPerSec}px`, width: `${(b - a) * pxPerSec}px` }}
          >
            <span className="cut-label" dir={t.dir}>
              {label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
