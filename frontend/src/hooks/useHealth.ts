import { useEffect, useState } from "react";
import { getHealth } from "../api/client";
import type { Health } from "../api/types";

export function useHealth(): { health: Health | null; error: string | null } {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getHealth()
      .then((h) => {
        if (alive) setHealth(h);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, []);

  return { health, error };
}
