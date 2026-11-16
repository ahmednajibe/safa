import { useCallback, useEffect, useState } from "react";
import { getOperations } from "../api/client";
import type { OpSpec, Preset } from "../api/types";

/** Dispatched (window event) after a model download finishes — every
 *  mounted rack refetches so previously-unavailable cards unlock
 *  without a reload (M10 T3). */
export const MODELS_CHANGED = "ss-models-changed";

export function notifyModelsChanged(): void {
  window.dispatchEvent(new CustomEvent(MODELS_CHANGED));
}

export function useOperations(): {
  ops: OpSpec[];
  presets: Record<string, Preset>;
  loaded: boolean;
} {
  const [ops, setOps] = useState<OpSpec[]>([]);
  const [presets, setPresets] = useState<Record<string, Preset>>({});
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(() => {
    getOperations()
      .then((o) => {
        setOps(o.operations);
        setPresets(o.presets);
        setLoaded(true);
      })
      .catch(() => {
        /* health error banner covers unreachable server */
      });
  }, []);

  useEffect(() => {
    let alive = true;
    const wrapped = () => {
      if (alive) load();
    };
    load();
    window.addEventListener(MODELS_CHANGED, wrapped);
    return () => {
      alive = false;
      window.removeEventListener(MODELS_CHANGED, wrapped);
    };
  }, [load]);

  return { ops, presets, loaded };
}
