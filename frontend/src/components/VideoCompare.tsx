import { useCallback, useEffect, useState } from "react";
import { useT } from "../i18n";

/** Display-only video mirror: a muted <video> follows the transport audio
 *  element — same playhead (±0.1 s), same play/pause state, same side the
 *  A/B control picked. Seeking stays cheap because the backend endpoints
 *  answer HTTP Range requests. */
function useMirror(
  videoEl: HTMLVideoElement | null,
  audioEl: HTMLMediaElement | null,
  active: boolean,
  map?: (t: number) => number,
) {
  useEffect(() => {
    if (!videoEl || !audioEl || !active) {
      videoEl?.pause();
      return;
    }
    const sync = () => {
      const t = map ? map(audioEl.currentTime) : audioEl.currentTime;
      if (Math.abs(videoEl.currentTime - t) > 0.1) {
        videoEl.currentTime = Math.max(0, t);
      }
    };
    const onPlay = () => {
      sync();
      void videoEl.play().catch(() => {});
    };
    const onPause = () => videoEl.pause();
    audioEl.addEventListener("play", onPlay);
    audioEl.addEventListener("pause", onPause);
    audioEl.addEventListener("seeked", sync);
    audioEl.addEventListener("timeupdate", sync);
    sync();
    if (audioEl.paused) videoEl.pause();
    else onPlay();
    return () => {
      audioEl.removeEventListener("play", onPlay);
      audioEl.removeEventListener("pause", onPause);
      audioEl.removeEventListener("seeked", sync);
      audioEl.removeEventListener("timeupdate", sync);
    };
  }, [videoEl, audioEl, active, map]);
}

/** Video well next to the waveform (§7): original or processed frame —
 *  whichever side A/B is on. With no processed video (previews, or
 *  keep-video off) the original stays up, positioned through the timeline
 *  map so the picture still matches the processed audio. Click toggles
 *  play like the transport button. The island is dir="ltr". */
export function VideoCompare({
  origUrl,
  procUrl,
  side,
  aEl,
  bEl,
  bToSrc,
  onToggle,
}: {
  origUrl: string;
  procUrl: string | null;
  side: "a" | "b";
  aEl: HTMLMediaElement | null;
  bEl: HTMLMediaElement | null;
  /** processed-side time → absolute source time (for picture-follows-B). */
  bToSrc: (t: number) => number;
  onToggle: () => void;
}) {
  const t = useT();
  const [vaEl, setVaEl] = useState<HTMLVideoElement | null>(null);
  const [vbEl, setVbEl] = useState<HTMLVideoElement | null>(null);
  const vaCb = useCallback((el: HTMLVideoElement | null) => setVaEl(el), []);
  const vbCb = useCallback((el: HTMLVideoElement | null) => setVbEl(el), []);

  const hasVb = procUrl !== null;
  const showA = side === "a" || !hasVb;

  // Original picture: driven by A when front, else follows B's clock
  // translated back into source time.
  useMirror(vaEl, showA ? aEl : bEl, showA, side === "a" ? undefined : bToSrc);
  useMirror(vbEl, bEl, side === "b" && hasVb);

  return (
    <div className="video-well" dir="ltr">
      <video
        ref={vaCb}
        className={`compare-video${showA ? "" : " behind"}`}
        src={origUrl}
        muted
        playsInline
        preload="auto"
      />
      {hasVb && (
        <video
          ref={vbCb}
          className={`compare-video proc${side === "b" ? "" : " behind"}`}
          src={procUrl}
          muted
          playsInline
          preload="auto"
        />
      )}
      <button
        type="button"
        className="video-hit"
        aria-label={t.d.well.video}
        onClick={onToggle}
      />
    </div>
  );
}
