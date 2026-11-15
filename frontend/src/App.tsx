import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { submitJob } from "./actions";
import { AnnounceContext, LiveAnnouncer } from "./components/LiveAnnouncer";
import type { TransportHandle } from "./components/AudioComparePlayer";
import { ContentFoot } from "./components/ContentFoot";
import { Dock } from "./components/Dock";
import { HistoryDrawer } from "./components/HistoryDrawer";
import { OpsRack } from "./components/OpsRack";
import { PlayerPanel } from "./components/PlayerPanel";
import { PresetBar } from "./components/PresetBar";
import { TopBar } from "./components/TopBar";
import { useEstimate } from "./hooks/useEstimate";
import { useMediaQuery } from "./hooks/useMediaQuery";
import { useHealth } from "./hooks/useHealth";
import { useJob } from "./hooks/useJob";
import { useJobs } from "./hooks/useJobs";
import { useMedia } from "./hooks/useMedia";
import { useOperations } from "./hooks/useOperations";
import { useResolve } from "./hooks/useResolve";
import { useSync } from "./hooks/useSync";
import { I18nProvider, makeI18n } from "./i18n";
import { shortGpuName } from "./lib/format";
import { StoreProvider, useStore } from "./state/store";

function isTypingTarget(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    el.isContentEditable ||
    e.isComposing
  );
}

function Shell() {
  const store = useStore();
  const { ui, media, selection, job } = store;
  const i18n = useMemo(() => makeI18n(ui.lang), [ui.lang]);

  // <html lang/dir> follows the store — no reload, focus/playback preserved.
  useEffect(() => {
    document.documentElement.lang = ui.lang;
    document.documentElement.dir = i18n.dir;
  }, [ui.lang, i18n.dir]);

  useSync();
  const { health, error: healthError } = useHealth();
  const { ops, presets, loaded } = useOperations();
  useResolve();
  const estimate = useEstimate();
  useMedia();
  useJob();
  const { jobs, refresh } = useJobs();

  const transportRef = useRef<TransportHandle>(null);
  const [announce, setAnnounce] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e)) return;
      const t = transportRef.current;
      if (!t) return;
      if (e.code === "Space" && !e.altKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        t.toggle();
      } else if (e.code === "KeyA" && !e.altKey && !e.ctrlKey && !e.metaKey) {
        t.side("a");
      } else if (e.code === "KeyB" && !e.altKey && !e.ctrlKey && !e.metaKey) {
        t.side("b");
      } else if (e.code === "BracketLeft" && e.altKey) {
        e.preventDefault();
        t.seek(-5);
      } else if (e.code === "BracketRight" && e.altKey) {
        e.preventDefault();
        t.seek(5);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const attention = jobs.filter((j) => j.state === "completed").length;
  const mediaReady = media.status === "ready";
  const hasOps = selection.effective.length > 0;
  // ≤600 px: the dock collapses to one row; keep-video / estimate / save
  // actions move into the content foot above it (never covers content).
  const compact = useMediaQuery("(max-width: 600px)");
  const gpuName =
    health?.device === "gpu" && health.gpu?.name
      ? shortGpuName(health.gpu.name)
      : null;

  return (
    <I18nProvider value={i18n}>
      <AnnounceContext.Provider value={setAnnounce}>
        <div className="app">
          <TopBar
            health={health}
            attentionCount={attention}
            historyOpen={ui.historyOpen}
            onToggleHistory={() =>
              store.dispatchUi({ type: "history", open: !ui.historyOpen })
            }
          />
          {healthError && (
            <div className="banner neg" role="alert">
              {i18n.d.err.serverUnreachable}
            </div>
          )}
          <div className="workspace">
            <OpsRack ops={ops} loaded={loaded} />
            <div className="player-col">
              <PresetBar ops={ops} presets={presets} loaded={loaded} />
              <PlayerPanel ref={transportRef} />
              {compact && (
                <ContentFoot
                  estimate={estimate}
                  gpuName={gpuName}
                  mediaReady={mediaReady}
                  hasOps={hasOps}
                />
              )}
            </div>
          </div>
          {ui.historyOpen && (
            <HistoryDrawer
              jobs={jobs}
              refresh={refresh}
              onClose={() => store.dispatchUi({ type: "history", open: false })}
            />
          )}
          <Dock
            estimate={estimate}
            gpuName={gpuName}
            mediaReady={mediaReady}
            hasOps={hasOps}
            compact={compact}
            onPreview={() => {
              const { start_s, end_s } = job.previewRegion;
              void submitJob(store, { preview: { start_s, end_s } });
            }}
            onProcess={() => void submitJob(store)}
          />
          <LiveAnnouncer message={announce} />
        </div>
      </AnnounceContext.Provider>
    </I18nProvider>
  );
}

export default function App() {
  return (
    <StoreProvider>
      <Shell />
    </StoreProvider>
  );
}
