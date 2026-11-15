/** Preview-region geometry shared by the drag handles, the numeric fields
 *  and the [ / ] nudge shortcuts. Times are absolute source seconds. */

export interface Region {
  start_s: number;
  end_s: number;
}

/** Shortest selectable preview — half a second keeps handles grabbable. */
export const MIN_REGION_S = 0.5;

export function clampRegion(
  r: Region,
  duration: number,
  minLen = MIN_REGION_S,
): Region {
  const dur = Math.max(0, duration);
  const s = Math.min(Math.max(r.start_s, 0), Math.max(0, dur - minLen));
  let e = Math.max(r.end_s, s + minLen);
  e = Math.min(e, dur);
  return { start_s: s, end_s: e };
}

export type DragPart = "start" | "end" | "body";

/** One drag step: for edge parts ``arg`` is the new absolute time; for the
 *  body it is the seconds delta since grab (applied to the grabbed
 *  region — drift-free). */
export function dragRegion(
  r: Region,
  part: DragPart,
  arg: number,
  duration: number,
  minLen = MIN_REGION_S,
): Region {
  const dur = Math.max(0, duration);
  if (part === "start") {
    const s = Math.min(Math.max(0, arg), r.end_s - minLen);
    return { start_s: Math.max(0, s), end_s: r.end_s };
  }
  if (part === "end") {
    const e = Math.max(Math.min(dur, arg), r.start_s + minLen);
    return { start_s: r.start_s, end_s: Math.min(e, dur) };
  }
  const d = Math.min(Math.max(arg, -r.start_s), Math.max(0, dur - r.end_s));
  return { start_s: r.start_s + d, end_s: r.end_s + d };
}

/** [ / ] nudge: shift the whole region by ``delta`` seconds, clamped so it
 *  slides to a stop at the file edges without shrinking. */
export function nudgeRegion(
  r: Region,
  delta: number,
  duration: number,
): Region {
  const dur = Math.max(0, duration);
  const d = Math.min(
    Math.max(delta, -r.start_s),
    Math.max(0, dur - r.end_s),
  );
  return { start_s: r.start_s + d, end_s: r.end_s + d };
}
