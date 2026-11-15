import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { mediaPlaybackUrl } from "../api/client";
import { useT } from "../i18n";
import { fmtTimecode } from "../lib/format";
import {
  isIdentity,
  nextKeptSrc,
  outToSrc,
  srcToOut,
  type TimelineData,
} from "../lib/timeline";
import { SegmentedControl } from "./SegmentedControl";

export interface TransportHandle {
  toggle: () => void;
  side: (s: "a" | "b") => void;
  seek: (delta: number) => void;
}

/** Audio-only A/B compare (M9a): two <audio> elements — the original
 *  playback URL and the completed job's primary output. Switching sides
 *  translates the playhead through the job's timeline map; landing inside
 *  a removed span jumps to the next kept point and announces it. */
export const AudioComparePlayer = forwardRef<
  TransportHandle,
  {
    mediaId: string;
    processedUrl: string | null;
    timeline: TimelineData | null;
    onAnnounce: (msg: string) => void;
  }
>(function AudioComparePlayer(
  { mediaId, processedUrl, timeline, onAnnounce },
  ref,
) {
  const t = useT();
  const aRef = useRef<HTMLAudioElement>(null);
  const bRef = useRef<HTMLAudioElement>(null);
  const [side, setSide] = useState<"a" | "b">("a");
  const [playing, setPlaying] = useState(false);
  const [pos, setPos] = useState(0);
  const [dur, setDur] = useState(0);

  const mapped = timeline !== null && !isIdentity(timeline);
  const hasB = processedUrl !== null;

  const active = () => (side === "a" ? aRef.current : bRef.current);

  const switchTo = (next: "a" | "b") => {
    if (next === side || (next === "b" && !hasB)) return;
    const from = active();
    const cur = from?.currentTime ?? 0;
    const wasPlaying = !from?.paused;
    from?.pause();
    const el = next === "a" ? aRef.current : bRef.current;
    if (el) {
      let dest = cur;
      if (mapped && timeline) {
        if (next === "b") {
          const out = srcToOut(timeline, cur);
          if (out === null) {
            const nks = nextKeptSrc(timeline, cur);
            dest = nks !== null ? (srcToOut(timeline, nks) ?? 0) : 0;
            onAnnounce(
              t.t(t.d.ab.jumped, { t: fmtTimecode(dest) }),
            );
          } else {
            dest = out;
          }
        } else {
          dest = outToSrc(timeline, cur);
        }
      }
      el.currentTime = dest;
      if (wasPlaying) void el.play().catch(() => {});
    }
    setSide(next);
  };

  const togglePlay = () => {
    const el = active();
    if (!el) return;
    if (el.paused) void el.play().catch(() => {});
    else el.pause();
  };

  const seekBy = (delta: number) => {
    const el = active();
    if (!el || !Number.isFinite(el.duration)) return;
    el.currentTime = Math.min(
      Math.max(0, el.currentTime + delta),
      el.duration,
    );
    setPos(el.currentTime);
  };

  useImperativeHandle(ref, () => ({
    toggle: togglePlay,
    side: switchTo,
    seek: seekBy,
  }));

  useEffect(() => {
    if (!hasB && side === "b") setSide("a");
  }, [hasB, side]);

  const pct = dur > 0 ? Math.min(1, pos / dur) : 0;

  return (
    <div className="compare">
      <audio
        ref={aRef}
        src={mediaPlaybackUrl(mediaId)}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => {
          if (side === "a") setPos(e.currentTarget.currentTime);
        }}
        onLoadedMetadata={(e) => {
          if (side === "a") setDur(e.currentTarget.duration || 0);
        }}
      />
      {hasB && (
        <audio
          ref={bRef}
          src={processedUrl}
          preload="metadata"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onTimeUpdate={(e) => {
            if (side === "b") setPos(e.currentTarget.currentTime);
          }}
          onLoadedMetadata={(e) => {
            if (side === "b") setDur(e.currentTarget.duration || 0);
          }}
        />
      )}

      <div className="transport">
        {/* LTR island (§12.1): play, current time, bar, duration never
            mirror in Arabic. */}
        <div className="transport-island" dir="ltr">
          <button
            type="button"
            className="icon-btn play-btn"
            aria-label={playing ? t.d.player.pause : t.d.player.play}
            onClick={togglePlay}
          >
            {playing ? (
              <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
                <rect x="6" y="5" width="4" height="14" fill="currentColor" />
                <rect x="14" y="5" width="4" height="14" fill="currentColor" />
              </svg>
            ) : (
              <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M7 4.8v14.4L19.5 12 7 4.8Z" fill="currentColor" />
              </svg>
            )}
          </button>

          <span className="tc mono">{fmtTimecode(pos)}</span>
          <div className="progress playback">
            <div
              className="progress-fill"
              style={{ inlineSize: `${pct * 100}%` }}
            />
          </div>
          <span className="tc mono">{fmtTimecode(dur)}</span>
        </div>

        {hasB && (
          <SegmentedControl
            label={t.d.ab.compareHint}
            small
            options={[
              { value: "a", label: t.d.ab.original },
              { value: "b", label: t.d.ab.processed },
            ]}
            value={side}
            onChange={(v) => switchTo(v as "a" | "b")}
          />
        )}
      </div>
    </div>
  );
});
