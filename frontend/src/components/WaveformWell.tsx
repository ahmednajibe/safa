import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
  type RefObject,
} from "react";
import WaveSurfer from "wavesurfer.js";
import { useT } from "../i18n";
import { jobPeaksUrl, mediaPeaksUrl } from "../api/client";
import { usePeaks } from "../hooks/usePeaks";
import { hatchSpans } from "../lib/peaks";
import type { Region } from "../lib/region";
import type { TimelineData } from "../lib/timeline";
import { CutHatchOverlay } from "./CutHatchOverlay";
import { PreviewRegionHandles } from "./PreviewRegionHandles";
import { TimeRuler } from "./TimeRuler";

const MIN_ZOOM = 1;
const MAX_ZOOM = 64;
const ZOOM_STEP = 1.6;

/** Canvas fillStyle can't resolve var(--token) — pull the computed value
 *  off the document root so wave colours honour the design tokens. */
function resolveColor(color: string): string {
  if (!color.startsWith("var(")) return color;
  const name = color.slice(4, -1).trim();
  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim() || color
  );
}

/** One wavesurfer instance bound to an existing media element, rendering
 *  backend peaks (never browser-decoded audio). */
function useWave(
  wsRef: MutableRefObject<WaveSurfer | null>,
  hostRef: RefObject<HTMLDivElement | null>,
  media: HTMLMediaElement | null,
  channels: number[][] | null,
  duration: number,
  color: string,
  onScroll: (v0: number, scrollLeft: number) => void,
  onReady: () => void,
): void {
  const scrollCb = useRef(onScroll);
  scrollCb.current = onScroll;
  const readyCb = useRef(onReady);
  readyCb.current = onReady;

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !media || !channels || !channels.length || !(duration > 0)) {
      return;
    }
    const ws = WaveSurfer.create({
      container: host,
      media,
      peaks: channels,
      duration,
      waveColor: resolveColor(color),
      progressColor: resolveColor(color),
      cursorColor: resolveColor("var(--txt)"),
      cursorWidth: 1,
      interact: true,
      dragToSeek: true,
      fillParent: true,
      hideScrollbar: true,
      normalize: false,
      height: "auto",
    });
    wsRef.current = ws;
    const un = ws.on("scroll", (v0, _v1, scrollLeft) => {
      scrollCb.current(v0, scrollLeft);
    });
    const unReady = ws.on("ready", () => readyCb.current());
    return () => {
      un();
      unReady();
      ws.destroy();
      wsRef.current = null;
    };
  }, [wsRef, hostRef, media, channels, duration, color]);
}

/** The waveform well (§6): original wave in --wave-original grey under the
 *  processed wave in --data amber; A/B swaps which layer is in front and
 *  interactive. Cut hatches + the preview region overlay ride on the
 *  original (source-time) wave. The whole well is a dir="ltr" island. */
export function WaveformWell({
  mediaId,
  srcDuration,
  aEl,
  bEl,
  compareJobId,
  front,
  timeline,
  srcOffset,
  region,
  onRegion,
}: {
  mediaId: string;
  srcDuration: number;
  /** The transport <audio> elements — wavesurfer drives their playhead. */
  aEl: HTMLMediaElement | null;
  bEl: HTMLMediaElement | null;
  compareJobId: string | null;
  front: "a" | "b";
  timeline: TimelineData | null;
  srcOffset: number;
  region: Region;
  onRegion: (r: Region) => void;
}) {
  const t = useT();
  const aPeaks = usePeaks(mediaId ? mediaPeaksUrl(mediaId) : null);
  const bPeaks = usePeaks(bEl && compareJobId ? jobPeaksUrl(compareJobId) : null);

  const stackRef = useRef<HTMLDivElement>(null);
  const aHostRef = useRef<HTMLDivElement>(null);
  const bHostRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [scrollX, setScrollX] = useState(0);
  const syncing = useRef(false);
  const wsA = useRef<WaveSurfer | null>(null);
  const wsB = useRef<WaveSurfer | null>(null);

  const durA = aPeaks?.durationS || srcDuration;
  const durB = bPeaks?.durationS || 0;

  useEffect(() => {
    const el = stackRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  // Keep the non-front wave panned to the same relative position so the
  // two stay visually registered when the user flips A/B.
  const syncFrom = useCallback(
    (self: "a" | "b", v0: number) => {
      if (syncing.current) return;
      const other = self === "a" ? wsB.current : wsA.current;
      const selfDur = self === "a" ? durA : durB;
      const otherDur = self === "a" ? durB : durA;
      if (!other || !(selfDur > 0) || !(otherDur > 0)) return;
      syncing.current = true;
      other.setScrollTime((v0 / selfDur) * otherDur);
      syncing.current = false;
    },
    [durA, durB],
  );

  const onScrollA = useCallback(
    (v0: number, scrollLeft: number) => {
      setScrollX(scrollLeft);
      syncFrom("a", v0);
    },
    [syncFrom],
  );
  const onScrollB = useCallback(
    (v0: number) => syncFrom("b", v0),
    [syncFrom],
  );

  // Zoom applies px/sec derived from each wave's own duration — both waves
  // stay full-width at zoom 1 and scroll together when zoomed in. The front
  // layer gets pointer interaction + visible cursor; the one behind is
  // inert. Both touches throw/no-op before the instance's async load
  // resolves, so they run here and again from each wave's 'ready' event.
  const applyWaveConfig = useCallback(() => {
    if (wsA.current?.getDecodedData()) {
      wsA.current.toggleInteraction(front === "a");
      wsA.current.setOptions({ cursorWidth: front === "a" ? 1 : 0 });
      if (width > 0 && durA > 0) {
        wsA.current.zoom((width / durA) * zoom);
      }
    }
    if (wsB.current?.getDecodedData()) {
      wsB.current.toggleInteraction(front === "b");
      wsB.current.setOptions({ cursorWidth: front === "b" ? 1 : 0 });
      if (width > 0 && durB > 0) {
        wsB.current.zoom((width / durB) * zoom);
      }
    }
  }, [width, zoom, durA, durB, front]);

  useWave(
    wsA,
    aHostRef,
    aEl,
    aPeaks?.channels ?? null,
    durA,
    "var(--wave-original)",
    onScrollA,
    applyWaveConfig,
  );
  useWave(
    wsB,
    bHostRef,
    bEl,
    bPeaks?.channels ?? null,
    durB,
    "var(--data)",
    onScrollB,
    applyWaveConfig,
  );

  useEffect(() => applyWaveConfig(), [applyWaveConfig]);

  const ppsA = width > 0 && durA > 0 ? (width / durA) * zoom : 0;
  const cutSpans = useMemo(
    () => (timeline ? hatchSpans(timeline, srcOffset) : []),
    [timeline, srcOffset],
  );

  return (
    <section className="well" dir="ltr" aria-label={t.d.well.waveform}>
      <div className="well-top">
        <TimeRuler duration={durA} pxPerSec={ppsA} scrollX={scrollX} />
        <div className="well-zoom">
          <button
            type="button"
            className="icon-btn"
            aria-label={t.d.well.zoomOut}
            onClick={() => setZoom((z) => Math.max(MIN_ZOOM, z / ZOOM_STEP))}
          >
            −
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label={t.d.well.zoomIn}
            onClick={() => setZoom((z) => Math.min(MAX_ZOOM, z * ZOOM_STEP))}
          >
            +
          </button>
        </div>
      </div>
      <div className="well-stack" ref={stackRef}>
        <div className={`wave-layer orig${front === "b" ? " behind" : ""}`}>
          <div ref={aHostRef} className="wave-surface" />
          {aPeaks ? (
            <>
              <CutHatchOverlay
                spans={cutSpans}
                pxPerSec={ppsA}
                scrollX={scrollX}
              />
              <PreviewRegionHandles
                region={region}
                duration={durA}
                pxPerSec={ppsA}
                scrollX={scrollX}
                onChange={onRegion}
              />
            </>
          ) : null}
        </div>
        {bPeaks ? (
          <div className={`wave-layer proc${front === "b" ? "" : " behind"}`}>
            <div ref={bHostRef} className="wave-surface" />
          </div>
        ) : null}
        {!aPeaks ? (
          <div className="well-empty hint">{t.d.well.loading}</div>
        ) : null}
      </div>
    </section>
  );
}
