/** SPEC §12 saving. Since v0.1.1 "Save as…" / "Save all to folder…" go
 * through the local server's native dialogs (actions.ts → /save-as,
 * /save-all): Chrome's File System Access picker cancelled saves itself
 * (AbortError after the user pressed Save) and Firefox has none. This is
 * only the fallback for a server without a GUI (Docker/headless): a plain
 * browser download. */

import { jobFileUrl } from "../api/client";

export function downloadJobFile(jobId: string, name: string): void {
  const a = document.createElement("a");
  a.href = jobFileUrl(jobId, name);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
