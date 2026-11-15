/** Global keyboard shortcuts — matched on ``event.code`` (physical key
 *  position) so they work on the Arabic keyboard layout, where KeyA
 *  produces ش and KeyP produces ح (UI_DESIGN §12.1). */

export type ShortcutAction =
  | "toggle"
  | "side-a"
  | "side-b"
  | "seek-back"
  | "seek-fwd"
  | "play-region"
  | "nudge-back"
  | "nudge-fwd"
  | "escape";

export function shortcutAction(
  e: Pick<
    KeyboardEvent,
    "code" | "altKey" | "ctrlKey" | "metaKey" | "defaultPrevented"
  >,
): ShortcutAction | null {
  // Chords and already-handled keys (roving radio groups, sliders) are out.
  if (e.altKey || e.ctrlKey || e.metaKey || e.defaultPrevented) return null;
  switch (e.code) {
    case "Space":
      return "toggle";
    case "KeyA":
      return "side-a";
    case "KeyB":
      return "side-b";
    case "KeyP":
      return "play-region";
    case "BracketLeft":
      return "nudge-back";
    case "BracketRight":
      return "nudge-fwd";
    case "ArrowLeft":
      return "seek-back";
    case "ArrowRight":
      return "seek-fwd";
    case "Escape":
      return "escape";
    default:
      return null;
  }
}

/** Inputs, textareas, selects, contenteditable and IME composition keep
 *  their keys — shortcuts stay out of the way while typing. */
export function isTypingTarget(e: KeyboardEvent): boolean {
  if (e.isComposing) return true;
  const el = e.target as HTMLElement | null;
  if (!el || typeof el.tagName !== "string") return false;
  return (
    el.tagName === "INPUT" ||
    el.tagName === "TEXTAREA" ||
    el.tagName === "SELECT" ||
    el.isContentEditable === true
  );
}
