import { describe, expect, it } from "vitest";
import { isTypingTarget, shortcutAction } from "./shortcuts";

const ev = (
  code: string,
  opts: Partial<{
    key: string;
    altKey: boolean;
    ctrlKey: boolean;
    metaKey: boolean;
    isComposing: boolean;
    defaultPrevented: boolean;
    target: EventTarget | null;
  }> = {},
) =>
  ({
    code,
    key: opts.key ?? code,
    altKey: opts.altKey ?? false,
    ctrlKey: opts.ctrlKey ?? false,
    metaKey: opts.metaKey ?? false,
    isComposing: opts.isComposing ?? false,
    defaultPrevented: opts.defaultPrevented ?? false,
    target: opts.target ?? null,
  }) as KeyboardEvent;

describe("shortcutAction via event.code (Arabic layout safe)", () => {
  it("maps physical codes regardless of the localized key", () => {
    // Arabic layout: KeyA produces ش, KeyB produces ل, KeyP produces ح
    expect(shortcutAction(ev("KeyA", { key: "ش" }))).toBe("side-a");
    expect(shortcutAction(ev("KeyB", { key: "ل" }))).toBe("side-b");
    expect(shortcutAction(ev("KeyP", { key: "ح" }))).toBe("play-region");
    expect(shortcutAction(ev("Space", { key: " " }))).toBe("toggle");
  });
  it("[ and ] nudge the preview region", () => {
    expect(shortcutAction(ev("BracketLeft"))).toBe("nudge-back");
    expect(shortcutAction(ev("BracketRight"))).toBe("nudge-fwd");
  });
  it("arrows seek 5 s without Alt", () => {
    expect(shortcutAction(ev("ArrowLeft"))).toBe("seek-back");
    expect(shortcutAction(ev("ArrowRight"))).toBe("seek-fwd");
  });
  it("Escape closes", () => {
    expect(shortcutAction(ev("Escape"))).toBe("escape");
  });
  it("ignores modified chords", () => {
    expect(shortcutAction(ev("KeyA", { ctrlKey: true }))).toBeNull();
    expect(shortcutAction(ev("KeyA", { metaKey: true }))).toBeNull();
    expect(shortcutAction(ev("KeyA", { altKey: true }))).toBeNull();
    expect(shortcutAction(ev("ArrowLeft", { altKey: true }))).toBeNull();
  });
  it("ignores handled (defaultPrevented) events — roving controls win", () => {
    expect(shortcutAction(ev("ArrowLeft", { defaultPrevented: true }))).toBeNull();
  });
  it("returns null for unmapped keys", () => {
    expect(shortcutAction(ev("KeyQ"))).toBeNull();
  });
});

describe("isTypingTarget", () => {
  it("true for form fields and IME composition", () => {
    const input = document.createElement("input");
    expect(isTypingTarget(ev("KeyA", { target: input }))).toBe(true);
    const ta = document.createElement("textarea");
    expect(isTypingTarget(ev("KeyA", { target: ta }))).toBe(true);
    const sel = document.createElement("select");
    expect(isTypingTarget(ev("KeyA", { target: sel }))).toBe(true);
    expect(isTypingTarget(ev("KeyA", { isComposing: true }))).toBe(true);
  });
  it("false for buttons and body", () => {
    const btn = document.createElement("button");
    expect(isTypingTarget(ev("KeyA", { target: btn }))).toBe(false);
    expect(isTypingTarget(ev("KeyA", { target: document.body }))).toBe(false);
  });
});
