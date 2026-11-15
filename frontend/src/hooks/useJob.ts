import { useEffect } from "react";
import { getJob } from "../api/client";
import type { Job } from "../api/types";
import { useStore } from "../state/store";

/** Follow the active job over SSE (GET /api/jobs/{id}/events): `state`
 *  events drive the dock; on `done` (or a hard error) refetch
 *  GET /api/jobs/{id} once for the final payload. EventSource's own
 *  reconnect covers transient drops — the endpoint replays full state. */
export function useJob(): void {
  const { job, dispatchJob } = useStore();
  const activeId = job.activeId;

  useEffect(() => {
    if (!activeId) return;
    let es: EventSource | null = null;
    let closed = false;
    let retried = false;

    const refetch = () => {
      if (closed) return;
      getJob(activeId)
        .then((j) => {
          if (!closed) dispatchJob({ type: "job-update", job: j });
        })
        .catch(() => {
          /* terminal refresh is best-effort; next state event wins */
        });
    };

    const open = () => {
      es = new EventSource(`/api/jobs/${activeId}/events`);
      es.addEventListener("state", (e) => {
        if (closed) return;
        try {
          const payload = JSON.parse((e as MessageEvent).data) as Job;
          dispatchJob({ type: "job-update", job: payload });
        } catch {
          /* malformed event — skip */
        }
      });
      es.addEventListener("done", () => {
        es?.close();
        refetch();
      });
      es.onerror = () => {
        // EventSource retries automatically while CONNECTING; if the
        // endpoint is gone entirely (CLOSED), refetch once via REST.
        if (es && es.readyState === EventSource.CLOSED && !retried) {
          retried = true;
          refetch();
        }
      };
    };

    open();
    return () => {
      closed = true;
      es?.close();
    };
  }, [activeId, dispatchJob]);
}
