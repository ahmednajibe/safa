import { useEffect, useState } from "react";
import { getOperations } from "../api/client";
import type { OpSpec, Preset } from "../api/types";

export function useOperations(): {
  ops: OpSpec[];
  presets: Record<string, Preset>;
  loaded: boolean;
} {
  const [ops, setOps] = useState<OpSpec[]>([]);
  const [presets, setPresets] = useState<Record<string, Preset>>({});
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    getOperations()
      .then((o) => {
        if (!alive) return;
        setOps(o.operations);
        setPresets(o.presets);
        setLoaded(true);
      })
      .catch(() => {
        /* health error banner covers unreachable server */
      });
    return () => {
      alive = false;
    };
  }, []);

  return { ops, presets, loaded };
}
