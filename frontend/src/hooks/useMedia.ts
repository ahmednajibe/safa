import { useEffect } from "react";
import { getMedia } from "../api/client";
import { useStore } from "../state/store";

const POLL_MS = 600;

/** While media.status === "preparing", poll GET /api/media/{id} until
 *  ready.peaks && lufs are in (or ready.error → media-error). */
export function useMedia(): void {
  const { media, dispatchMedia } = useStore();
  const mediaId = media.mediaId;
  const preparing = media.status === "preparing";

  useEffect(() => {
    if (!preparing || !mediaId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const m = await getMedia(mediaId);
        if (!alive) return;
        if (m.ready?.error) {
          dispatchMedia({
            type: "media-error",
            code: "prep_failed",
            message: m.ready.error,
          });
          return;
        }
        if (m.ready?.peaks && m.lufs != null) {
          dispatchMedia({ type: "media-ready", media: m });
          return;
        }
      } catch {
        /* transient → keep polling */
      }
      if (alive) timer = setTimeout(tick, POLL_MS);
    };

    timer = setTimeout(tick, POLL_MS);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [preparing, mediaId, dispatchMedia]);
}
