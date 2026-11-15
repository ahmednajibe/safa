/** Session restore rules (M9a fix): the dock's job belongs to the restored
 *  media — a fresh tab must never show another file's results under an
 *  empty drop zone; finished jobs of other files live only in History. */

import type { Job } from "../api/types";

export const SS_MEDIA = "ss.mediaId";
export const SS_JOB = "ss.jobId";

/** The newest job a session may adopt for the dock: only one belonging to
 *  the restored media. No restored media → nothing to adopt. */
export function pickHydrationJob(
  jobs: Job[],
  mediaId: string | null,
): Job | null {
  if (!mediaId) return null;
  return jobs.find((j) => j.media_id === mediaId) ?? null;
}
