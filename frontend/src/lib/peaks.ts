/** Waveform helpers: backend peaks → wavesurfer channels, timeline → cut
 *  hatches, ruler tick placement. Pure — no DOM. */

import { removedSpans, type TimelineData } from "./timeline";

const EPS = 1e-9;

/** Backend peaks are ``data[ch] = [[min, max], …]`` — one pair per bucket.
 *  wavesurfer wants per-channel sample arrays, so pairs are flattened to
 *  alternating min/max values. Long payloads are folded down to
 *  ``maxPairsPerChannel`` columns so a 60-minute file renders in well under
 *  a second once peaks arrive (min-of-mins / max-of-maxes keeps the
 *  envelope exact). */
export function flattenPeaks(
  data: number[][][],
  maxPairsPerChannel = 60_000,
): number[][] {
  return (data ?? []).map((pairs) => {
    const n = pairs?.length ?? 0;
    if (!n) return [];
    const stride = Math.max(1, Math.ceil(n / maxPairsPerChannel));
    const out: number[] = [];
    for (let i = 0; i < n; i += stride) {
      let mn = Infinity;
      let mx = -Infinity;
      const end = Math.min(n, i + stride);
      for (let j = i; j < end; j++) {
        const p = pairs[j];
        if (p[0] < mn) mn = p[0];
        if (p[1] > mx) mx = p[1];
      }
      out.push(mn === Infinity ? 0 : mn, mx === -Infinity ? 0 : mx);
    }
    return out;
  });
}

/** Spans to hatch on the ORIGINAL waveform, in absolute source seconds.
 *  Full jobs: every unkept span is a real removal — head and tail count
 *  (trim). Preview jobs: the timeline's source domain is the previewed
 *  slice, so every local removed span is a real cut inside that slice —
 *  including local head/tail; outside the slice is "not covered", not
 *  "removed". ``srcOffset`` (the preview's start_s) shifts local times to
 *  absolute source time. */
export function hatchSpans(
  tl: TimelineData,
  srcOffset = 0,
): [number, number][] {
  return removedSpans(tl).map(
    ([a, b]) => [a + srcOffset, b + srcOffset] as [number, number],
  );
}

const NICE_STEPS = [
  0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600,
];

/** Ruler tick times: smallest "nice" step keeping ≳80 px between labels —
 *  4–8 labels at fit zoom, denser as the user zooms in. */
export function rulerTicks(duration: number, pxPerSec: number): number[] {
  const minStep = 80 / Math.max(pxPerSec, EPS);
  let step = NICE_STEPS[NICE_STEPS.length - 1];
  for (const s of NICE_STEPS) {
    if (s >= minStep) {
      step = s;
      break;
    }
  }
  while (step < minStep) step *= 2;
  const ticks: number[] = [];
  for (let t = 0; t <= duration + EPS; t += step) {
    ticks.push(Math.min(t, duration));
  }
  if (ticks[ticks.length - 1] !== duration && duration - ticks[ticks.length - 1] > step * 0.5) {
    ticks.push(duration);
  }
  return ticks;
}
