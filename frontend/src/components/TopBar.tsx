import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Health } from "../api/types";
import { useT } from "../i18n";
import { shortGpuName } from "../lib/format";
import { useStore } from "../state/store";
import { SettingsPopover } from "./SettingsPopover";

/** Stroke icons — 16 px, 1.5 stroke, currentColor (same weight family as
 *  the surrounding text so they read as proper glyphs, not dots). */
function Icon({ children, size = 16 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

const GEAR = (
  <>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
  </>
);
const HISTORY = (
  <>
    <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
    <path d="M3 3v5h5" />
    <path d="M12 7v5l4 2" />
  </>
);
const HELP = (
  <>
    <circle cx="12" cy="12" r="10" />
    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
    <path d="M12 17h.01" />
  </>
);

export function TopBar({
  health,
  attentionCount,
  onToggleHistory,
  historyOpen,
}: {
  health: Health | null;
  attentionCount: number;
  onToggleHistory: () => void;
  historyOpen: boolean;
}) {
  const t = useT();
  const { media, ui, dispatchUi } = useStore();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement>(null);
  const helpRef = useRef<HTMLDivElement>(null);

  // outside click / Escape closes popovers
  useEffect(() => {
    if (!settingsOpen && !helpOpen) return;
    const onDown = (e: MouseEvent) => {
      if (settingsOpen && settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setSettingsOpen(false);
      }
      if (helpOpen && helpRef.current && !helpRef.current.contains(e.target as Node)) {
        setHelpOpen(false);
      }
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        setSettingsOpen(false);
        setHelpOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [settingsOpen, helpOpen]);

  const gpuReady = health?.device === "gpu" && !!health.gpu?.name;
  const gpuShort = gpuReady ? shortGpuName(health.gpu!.name) : null;

  return (
    <header className="topbar">
      <span className="brand" aria-hidden="true">
        <svg width="17" height="17" viewBox="0 0 20 20">
          <rect x="1" y="7" width="3" height="6" rx="1" fill="currentColor" />
          <rect x="6" y="4" width="3" height="12" rx="1" fill="currentColor" />
          <rect x="11" y="7" width="3" height="6" rx="1" fill="currentColor" />
          <rect x="16" y="9" width="3" height="3" rx="1" fill="currentColor" />
        </svg>
      </span>
      <h1 className="brand-name">{t.d.appName}</h1>

      {media.filename && (
        <>
          <span className="dot-sep" aria-hidden="true">
            ·
          </span>
          <span className="top-filename">
            <bdi>{media.filename}</bdi>
          </span>
        </>
      )}

      <span className="ms-auto" />

      <button
        type="button"
        className="lang-btn"
        onClick={() =>
          dispatchUi({ type: "lang", lang: ui.lang === "en" ? "ar" : "en" })
        }
        aria-label={t.d.topBar.lang}
      >
        {ui.lang === "en" ? "عربي" : "EN"}
      </button>

      <div className="pop-wrap" ref={settingsRef}>
        <button
          type="button"
          className="icon-btn"
          aria-label={t.d.topBar.settings}
          aria-expanded={settingsOpen}
          onClick={() => {
            setSettingsOpen((v) => !v);
            setHelpOpen(false);
          }}
        >
          <Icon>{GEAR}</Icon>
        </button>
        {settingsOpen && <SettingsPopover />}
      </div>

      <div className="pop-wrap" ref={helpRef}>
        <button
          type="button"
          className="icon-btn"
          aria-label={t.d.topBar.shortcuts}
          aria-expanded={helpOpen}
          onClick={() => {
            setHelpOpen((v) => !v);
            setSettingsOpen(false);
          }}
        >
          <Icon>{HELP}</Icon>
        </button>
        {helpOpen && (
          <div className="popover help-pop" role="dialog" aria-label={t.d.shortcuts.title}>
            <div className="pop-title">{t.d.shortcuts.title}</div>
            <ul className="help-list">
              <li><kbd>Space</kbd> {t.d.shortcuts.play}</li>
              <li><kbd>A</kbd> {t.d.shortcuts.original}</li>
              <li><kbd>B</kbd> {t.d.shortcuts.processed}</li>
              <li><kbd>Alt</kbd>+<kbd>[</kbd> / <kbd>Alt</kbd>+<kbd>]</kbd> {t.d.shortcuts.seek}</li>
            </ul>
          </div>
        )}
      </div>

      <button
        type="button"
        className="icon-btn"
        aria-label={t.d.topBar.history}
        aria-expanded={historyOpen}
        onClick={onToggleHistory}
      >
        <Icon>{HISTORY}</Icon>
        {attentionCount > 0 && (
          <span className="badge">{t.num(attentionCount)}</span>
        )}
      </button>

      <span
        className="hardware-pill"
        title={gpuReady ? (health?.gpu?.name ?? undefined) : undefined}
      >
        <span className="hw-long">
          {gpuShort ? `${gpuShort} · ${t.d.device.gpuReady}` : t.d.device.cpuOnly}
        </span>
        <span className="hw-short">{gpuReady ? "GPU" : "CPU"}</span>
      </span>
    </header>
  );
}
