/** Media prep contract helpers. */

import type { MediaInfo } from "../api/types";

/** Whether media prep is done and the file is usable.
 *
 * ``ready.peaks`` is the backend's prep-done marker — ``peaks.json`` is
 * written last, after the loudness pass — so it alone is the gate.
 * ``lufs`` is NOT: a silent recording measures ``null`` forever and an
 * earlier ``lufs != null`` check left such uploads stuck in "preparing"
 * (R2 #13a). */
export function isMediaReady(m: MediaInfo): boolean {
  return m.ready?.peaks === true;
}
