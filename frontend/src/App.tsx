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
import { isTypingTarget, shortcutAction } from "./lib/shortcuts";
import { StoreProvider, useStore } from "./state/store";

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

  // Shortcuts match on event.code (physical position) so they keep working
  // on the Arabic layout (§8). Space/[ preventDefault so the page doesn't
  // scroll.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e)) return;
      const action = shortcutAction(e);
      if (!action) return;
      if (action === "escape") {
        if (ui.historyOpen) {
          e.preventDefault();
          store.dispatchUi({ type: "history", open: false });
        }
        return;
      }
      const t = transportRef.current;
      if (!t) return;
      switch (action) {
        case "toggle":
          e.preventDefault();
          t.toggle();
          break;
        case "side-a":
          t.side("a");
          break;
        case "side-b":
          t.side("b");
          break;
        case "seek-back":
          t.seek(-5);
          break;
        case "seek-fwd":
          t.seek(5);
          break;
        case "play-region":
          t.playRegion();
          break;
        case "nudge-back":
          t.nudgeRegion(-0.5);
          break;
        case "nudge-fwd":
          t.nudgeRegion(0.5);
          break;
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [ui.historyOpen, store]);

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
