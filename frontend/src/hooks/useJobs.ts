import { useCallback, useEffect, useState } from "react";
import { getJobs } from "../api/client";
import type { Job } from "../api/types";
import { isTerminal } from "../state/reducers";
import { useStore } from "../state/store";

/** Recent job list for the history drawer + the topbar attention badge.
 *  Refetches whenever the active job lands in a terminal state. */
export function useJobs(): { jobs: Job[]; refresh: () => void } {
  const { job } = useStore();
  const [jobs, setJobs] = useState<Job[]>([]);
  const refresh = useCallback(() => {
    getJobs()
      .then((r) => setJobs(r.jobs))
      .catch(() => {});
  }, []);

  useEffect(refresh, [refresh]);
  useEffect(() => {
    if (job.job && isTerminal(job.job)) refresh();
  }, [job.job?.state, refresh]);

  return { jobs, refresh };
}
