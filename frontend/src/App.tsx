import { useEffect, useState } from "react";
import { getHealth, getOperations } from "./api/client";
import type { Health, OpSpec, Preset } from "./api/types";
import Dock from "./components/Dock";
import OpsRack from "./components/OpsRack";
import PlayerPanel from "./components/PlayerPanel";
import TopBar from "./components/TopBar";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [ops, setOps] = useState<OpSpec[]>([]);
  const [presets, setPresets] = useState<Record<string, Preset>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([getHealth(), getOperations()])
      .then(([h, o]) => {
        if (!alive) return;
        setHealth(h);
        setOps(o.operations);
        setPresets(o.presets);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="flex h-dvh flex-col bg-ground font-sans text-text">
      <TopBar health={health} />
      {error && (
        <div className="mx-4 mb-3 flex items-center gap-3 rounded-inner bg-surface px-4 py-3 lg:mx-3.5">
          <span className="pill pill-negative">Server not reachable</span>
          <span className="text-small text-muted">{error}</span>
        </div>
      )}
      <main className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 pb-4 lg:grid lg:grid-cols-[400px_minmax(0,1fr)] lg:gap-3.5 lg:overflow-hidden lg:px-3.5 lg:pb-3.5">
        <div className="order-1 min-h-0 shrink-0 lg:order-2">
          <PlayerPanel />
        </div>
        <div className="order-2 min-h-0 shrink-0 lg:order-1">
          <OpsRack ops={ops} presets={presets} health={health} />
        </div>
      </main>
      <Dock />
    </div>
  );
}
