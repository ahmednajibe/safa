import { useEffect, useState } from "react";
import { flattenPeaks } from "../lib/peaks";

export interface PeaksData {
  /** Per-channel sample arrays for wavesurfer (alternating min/max). */
  channels: number[][];
  /** Real media duration in seconds (the waveform's time domain). */
  durationS: number;
}

interface PeaksPayload {
  duration_s?: number;
  data?: number[][][];
}

/** Fetch backend peaks JSON and shape it for wavesurfer — the browser
 *  never decodes audio, it only reads the pre-computed [min,max] pairs. */
export function usePeaks(url: string | null): PeaksData | null {
  const [peaks, setPeaks] = useState<PeaksData | null>(null);

  useEffect(() => {
    if (!url) {
      setPeaks(null);
      return;
    }
    let alive = true;
    fetch(url, { headers: { Accept: "application/json" } })
      .then((r) => {
        if (!r.ok) return Promise.reject(new Error(String(r.status)));
        return r.json() as Promise<PeaksPayload>;
      })
      .then((j) => {
        if (!alive) return;
        if (!Array.isArray(j.data) || !j.data.length) {
          setPeaks(null);
          return;
        }
        setPeaks({
          channels: flattenPeaks(j.data),
          durationS: Number(j.duration_s) || 0,
        });
      })
      .catch(() => {
        if (alive) setPeaks(null);
      });
    return () => {
      alive = false;
    };
  }, [url]);

  return peaks;
}
