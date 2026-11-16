import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { mediaFileUrl, mediaPlaybackUrl } from "../api/client";
import { useT } from "../i18n";
import { fmtTimecode } from "../lib/format";
import {
  nudgeRegion as nudgeRegionBy,
  type Region,
} from "../lib/region";
import {
  needsTranslation,
  nextKeptSrc,
  outToSrc,
  srcToOut,
  type TimelineData,
} from "../lib/timeline";
import { SegmentedControl } from "./SegmentedControl";
import { VideoCompare } from "./VideoCompare";
import { WaveformWell } from "./WaveformWell";

export interface TransportHandle {
  toggle: () => void;
  side: (s: "a" | "b") => void;
  seek: (delta: number) => void;
  playRegion: () => void;
  nudgeRegion: (delta: number) => void;
}

/** The transport + waveform well + video well (M9b): two <audio> elements —
 *  the original playback URL and the compare job's primary output. The
 *  waves render backend peaks through wavesurfer (never browser-decoded),
 *  cut hatches + the draggable preview region ride on the original wave,
 *  and A/B swaps which wave — and which video frame — sits in front.
 *
 *  ``srcOffset`` is the compare job's preview start: preview jobs store a
 *  region-local timeline, so source-side lookups shift by it. */
export const AudioComparePlayer = forwardRef<
  TransportHandle,
  {
    mediaId: string;
    srcDuration: number;
    hasVideo: boolean;
    compareJobId: string | null;
    processedUrl: string | null;
    processedVideoUrl: string | null;
    timeline: TimelineData | null;
    srcOffset: number;
    region: Region;
    onRegion: (r: Region) => void;
    onAnnounce: (msg: string) => void;
  }
>(function AudioComparePlayer(
  {
    mediaId,
    srcDuration,
    hasVideo,
    compareJobId,
    processedUrl,
    processedVideoUrl,
    timeline,
    srcOffset,
    region,
    onRegion,
    onAnnounce,
  },
  ref,
) {
  const t = useT();
  const aRef = useRef<HTMLAudioElement | null>(null);
  const bRef = useRef<HTMLAudioElement | null>(null);
  // Element-state mirrors of the refs: wavesurfer + the video mirror
  // re-run their effects when the media elements mount/change.
  const [aEl, setAEl] = useState<HTMLAudioElement | null>(null);
  const [bEl, setBEl] = useState<HTMLAudioElement | null>(null);
  const aCb = useCallback((el: HTMLAudioElement | null) => {
    aRef.current = el;
    setAEl(el);
  }, []);
  const bCb = useCallback((el: HTMLAudioElement | null) => {
    bRef.current = el;
    setBEl(el);
  }, []);
  const regionEnd = useRef<number | null>(null);
  const regionStart = useRef<number | null>(null);

  const [side, setSide] = useState<"a" | "b">("a");
  const [playing, setPlaying] = useState(false);
  const [pos, setPos] = useState(0);
  const [dur, setDur] = useState(0);

  const mapped = needsTranslation(timeline, srcOffset);
  const hasB = processedUrl !== null;

  // source-domain (absolute s) → output-domain of the compare job.
  const toOut = useCallback(
    (srcT: number) => (timeline ? srcToOut(timeline, srcT - srcOffset) : srcT),
    [timeline, srcOffset],
  );
  const toSrc = useCallback(
    (outT: number) =>
      timeline ? outToSrc(timeline, outT) + srcOffset : outT,
    [timeline, srcOffset],
  );
  const nextKept = useCallback(
    (srcT: number) => {
      if (!timeline) return srcT;
      const n = nextKeptSrc(timeline, srcT - srcOffset);
      return n === null ? null : n + srcOffset;
    },
    [timeline, srcOffset],
  );
  const bToSrc = useMemo(() => {
    if (!mapped) return (outT: number) => outT;
    return (outT: number) => toSrc(outT);
  }, [mapped, toSrc]);

  const active = () => (side === "a" ? aRef.current : bRef.current);

  const switchTo = (next: "a" | "b") => {
    if (next === side || (next === "b" && !hasB)) return;
    regionEnd.current = null;
    regionStart.current = null;
    const from = active();
    const cur = from?.currentTime ?? 0;
    const wasPlaying = !from?.paused;
    from?.pause();
    const el = next === "a" ? aRef.current : bRef.current;
    if (el) {
      let dest = cur;
      if (mapped) {
        if (next === "b") {
          const out = toOut(cur);
          if (out === null) {
            const nks = nextKept(cur);
            dest = nks !== null ? (toOut(nks) ?? 0) : 0;
            onAnnounce(t.t(t.d.ab.jumped, { t: fmtTimecode(dest) }));
          } else {
            dest = out;
          }
        } else {
          dest = toSrc(cur);
        }
      }
      el.currentTime = Math.max(0, dest);
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
    regionEnd.current = null;
    el.currentTime = Math.min(
      Math.max(0, el.currentTime + delta),
      el.duration,
    );
    setPos(el.currentTime);
  };

  /** P: play just the previewed slice on the original side — the
   *  timeupdate handler pauses at region end. The 'seeking' event fired
   *  by the currentTime assignment is async, so onSeeking only cancels
   *  the armed region when the playhead actually moves away from its
   *  start (a real user/programmatic seek elsewhere). */
  const playRegion = () => {
    const el = aRef.current;
    if (!el) return;
    if (side !== "a") switchTo("a");
    el.currentTime = Math.min(
      Math.max(0, region.start_s),
      Number.isFinite(el.duration) ? el.duration : Infinity,
    );
    regionStart.current = region.start_s;
    regionEnd.current = region.end_s;
    void el.play().catch(() => {});
  };

  const nudgeRegion = (delta: number) => {
    onRegion(nudgeRegionBy(region, delta, srcDuration));
  };

  useImperativeHandle(ref, () => ({
    toggle: togglePlay,
    side: switchTo,
    seek: seekBy,
    playRegion,
    nudgeRegion,
  }));

  useEffect(() => {
    if (!hasB && side === "b") setSide("a");
  }, [hasB, side]);

  /** Region playback stops at the slice's end; a real seek cancels it. */
  const onTick = (el: HTMLAudioElement, isSide: boolean) => {
    if (
      regionEnd.current !== null &&
      el.currentTime >= regionEnd.current
    ) {
      regionEnd.current = null;
      regionStart.current = null;
      el.pause();
    }
    if (isSide) setPos(el.currentTime);
  };

  const pct = dur > 0 ? Math.min(1, pos / dur) : 0;

  return (
    <div className="compare">
      <audio
        ref={aCb}
        src={mediaPlaybackUrl(mediaId)}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => {
          setPlaying(false);
          regionEnd.current = null;
          regionStart.current = null;
        }}
        onEnded={() => setPlaying(false)}
        onSeeking={(e) => {
          if (
            regionEnd.current !== null &&
            regionStart.current !== null &&
            Math.abs(e.currentTarget.currentTime - regionStart.current) >
              0.75
          ) {
            regionEnd.current = null;
            regionStart.current = null;
          }
        }}
        onTimeUpdate={(e) => onTick(e.currentTarget, side === "a")}
        onLoadedMetadata={(e) => {
          if (side === "a") setDur(e.currentTarget.duration || 0);
        }}
      />
      {hasB && (
        <audio
          ref={bCb}
          src={processedUrl}
          preload="metadata"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onTimeUpdate={(e) => onTick(e.currentTarget, side === "b")}
          onLoadedMetadata={(e) => {
            if (side === "b") setDur(e.currentTarget.duration || 0);
          }}
        />
      )}

      <div className="compare-media">
        {hasVideo && (
          <VideoCompare
            origUrl={mediaFileUrl(mediaId)}
            procUrl={processedVideoUrl}
            side={side}
            aEl={aEl}
            bEl={bEl}
            bToSrc={bToSrc}
            onToggle={togglePlay}
          />
        )}
        <WaveformWell
          mediaId={mediaId}
          srcDuration={srcDuration}
          aEl={aEl}
          bEl={bEl}
          compareJobId={compareJobId}
          front={side}
          timeline={timeline}
          srcOffset={srcOffset}
          region={region}
          onRegion={onRegion}
        />
      </div>

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

        <button
          type="button"
          className="btn-secondary region-play"
          onClick={playRegion}
        >
          {t.d.preview.listenRegion}
        </button>

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
