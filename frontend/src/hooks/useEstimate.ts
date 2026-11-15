import { useEffect, useState } from "react";
import { getEstimate } from "../api/client";
import type { Estimate } from "../api/types";
import { useStore } from "../state/store";

const DEBOUNCE_MS = 250;

/** GET /api/estimate for the dock subline — same debounce/abort/discard
 *  rules as useResolve (stale estimates must not overwrite fresh ones). */
export function useEstimate(): Estimate | null {
  const { media, selection, ui } = useStore();
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const mediaId = media.status === "ready" ? media.mediaId : null;
  const opsJson = JSON.stringify(selection.effective);
  const device = ui.device;

  useEffect(() => {
    if (!mediaId) {
      setEstimate(null);
      return;
    }
    const ctl = new AbortController();
    const timer = setTimeout(() => {
      getEstimate(mediaId, JSON.parse(opsJson), device, ctl.signal)
        .then((e) => {
          if (!ctl.signal.aborted) setEstimate(e);
        })
        .catch(() => {
          if (!ctl.signal.aborted) setEstimate(null);
        });
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctl.abort();
    };
  }, [mediaId, opsJson, device]);

  return estimate;
}
