import { useEffect, useState } from "react";
import { getTimeline } from "../api/client";
import type { TimelineData } from "../api/types";

/** /api/jobs/{id}/timeline for the A/B player + cut list (404 until the
 *  job completes → stays null; identity maps arrive after completion). */
export function useTimelineMap(jobId: string | null): TimelineData | null {
  const [tl, setTl] = useState<TimelineData | null>(null);

  useEffect(() => {
    if (!jobId) {
      setTl(null);
      return;
    }
    let alive = true;
    getTimeline(jobId)
      .then((t) => {
        if (alive) setTl(t);
      })
      .catch(() => {
        if (alive) setTl(null);
      });
    return () => {
      alive = false;
    };
  }, [jobId]);

  return tl;
}
