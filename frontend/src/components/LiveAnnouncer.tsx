import { createContext, useContext } from "react";

export const AnnounceContext = createContext<(msg: string) => void>(() => {});

export function useAnnounce(): (msg: string) => void {
  return useContext(AnnounceContext);
}

/** aria-live polite region — jump/done announcements (§6 accessibility). */
export function LiveAnnouncer({ message }: { message: string }) {
  return (
    <div className="sr-only" aria-live="polite" role="status">
      {message}
    </div>
  );
}
