import { useEffect } from "react";
import { resolveOps } from "../api/client";
import { useStore } from "../state/store";

const DEBOUNCE_MS = 250;

/** Debounced POST /api/operations/resolve on every selection change —
 *  AbortController cancels in-flight calls; stale responses discarded. */
export function useResolve(): void {
  const { selection, dispatchSelection } = useStore();
  const opsJson = JSON.stringify(selection.ops);

  useEffect(() => {
    const ctl = new AbortController();
    const timer = setTimeout(() => {
      resolveOps(JSON.parse(opsJson), ctl.signal)
        .then((res) => {
          if (!ctl.signal.aborted) {
            dispatchSelection({
              type: "resolved",
              ops: res.ops,
              disabled: res.disabled,
            });
          }
        })
        .catch(() => {
          /* keep last resolve on transient failure */
        });
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctl.abort();
    };
  }, [opsJson, dispatchSelection]);
}
