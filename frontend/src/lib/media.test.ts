import { describe, expect, it } from "vitest";
import type { MediaInfo } from "../api/types";
import { isMediaReady } from "./media";

const base: MediaInfo = {
  media_id: "a".repeat(32),
  filename: "rec.wav",
  size_bytes: 100,
  probe: {
    duration_s: 2,
    format_name: "wav",
    size_bytes: 100,
    audio: [],
    video: [],
    has_video: false,
  },
  lufs: null,
  ready: { peaks: false, playback: false, error: null },
};

describe("isMediaReady", () => {
  it("is true once peaks exist even when lufs is null (silent file)", () => {
    // R2 #13a: integrated_lufs returns null on silence — the old
    // `lufs != null` gate kept such uploads in "preparing" forever.
    const m: MediaInfo = {
      ...base,
      ready: { peaks: true, playback: true, error: null },
      lufs: null,
    };
    expect(isMediaReady(m)).toBe(true);
  });

  it("is false while prep is still running", () => {
    expect(isMediaReady(base)).toBe(false);
    expect(
      isMediaReady({ ...base, lufs: -20.5 }),
    ).toBe(false);
  });

  it("is false when prep failed (error handled by the caller)", () => {
    const m: MediaInfo = {
      ...base,
      ready: { peaks: false, playback: false, error: "boom" },
    };
    expect(isMediaReady(m)).toBe(false);
  });
});
