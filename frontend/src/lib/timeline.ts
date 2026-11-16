/** Source↔output time map (TS port of backend/media/timeline.py).
 *  Used by the A/B player to translate playhead position across cuts. */

import type { TimelineData, TimelineSegment } from "../api/types";

export type { TimelineData, TimelineSegment };

const EPS = 1e-9;

export function srcToOut(tl: TimelineData, t: number): number | null {
  if (t < -EPS || t > tl.source_duration + EPS) return null;
  for (const seg of tl.segments) {
    if (t < seg.src_start - EPS) return null; // inside a removed gap
    if (t <= seg.src_end + EPS) {
      const src = Math.min(Math.max(t, seg.src_start), seg.src_end);
      return seg.out_start + (src - seg.src_start) / seg.speed;
    }
  }
  return null;
}

export function outToSrc(tl: TimelineData, t: number): number {
  const segs = tl.segments;
  if (!segs.length) return 0;
  if (t <= 0) return segs[0].src_start;
  for (const seg of segs) {
    if (t < seg.out_end - EPS) {
      const src = seg.src_start + (t - seg.out_start) * seg.speed;
      return Math.min(Math.max(src, seg.src_start), seg.src_end);
    }
  }
  return segs[segs.length - 1].src_end;
}

export function nextKeptSrc(tl: TimelineData, t: number): number | null {
  for (const seg of tl.segments) {
    if (t <= seg.src_end + EPS) return Math.max(t, seg.src_start);
  }
  return null;
}

export function removedSpans(tl: TimelineData): [number, number][] {
  const kept: [number, number][] = [];
  for (const s of tl.segments) {
    const last = kept[kept.length - 1];
    if (last && s.src_start - last[1] <= EPS) last[1] = Math.max(last[1], s.src_end);
    else kept.push([s.src_start, s.src_end]);
  }
  const out: [number, number][] = [];
  let pos = 0;
  for (const [a, b] of kept) {
    if (a - pos > EPS) out.push([pos, a]);
    pos = Math.max(pos, b);
  }
  if (tl.source_duration - pos > EPS) out.push([pos, tl.source_duration]);
  return out;
}

export function isIdentity(tl: TimelineData): boolean {
  const s = tl.segments;
  return (
    s.length === 1 &&
    Math.abs(s[0].src_start) <= EPS &&
    Math.abs(s[0].src_end - tl.source_duration) <= EPS &&
    Math.abs(s[0].speed - 1) <= EPS
  );
}

/** Whether A/B needs time translation at all: real cuts, or a preview
 *  timeline whose source domain is shifted by ``srcOffset`` — a preview
 *  job's map is region-local even when it is "identity", so an uncut
 *  preview still translates by its start_s (R2 #13b). */
export function needsTranslation(
  tl: TimelineData | null,
  srcOffset = 0,
): boolean {
  return tl !== null && (!isIdentity(tl) || srcOffset !== 0);
}
