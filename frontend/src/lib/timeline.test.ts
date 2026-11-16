import { describe, expect, it } from "vitest";
import {
  needsTranslation,
  nextKeptSrc,
  outToSrc,
  removedSpans,
  srcToOut,
  type TimelineData,
} from "./timeline";

// Same fixture shape as backend timeline.to_dict(): two kept spans with a
// removed gap in the middle (source 0–10, cut 3–7 → out 0–3,7–10 → 0–7).
const TL: TimelineData = {
  source_duration: 10,
  segments: [
    { src_start: 0, src_end: 3, out_start: 0, out_end: 3, speed: 1 },
    { src_start: 7, src_end: 10, out_start: 3, out_end: 6, speed: 1 },
  ],
  removed: [[3, 7]],
};

const SPEED: TimelineData = {
  source_duration: 10,
  segments: [
    { src_start: 0, src_end: 10, out_start: 0, out_end: 5, speed: 2 },
  ],
  removed: [],
};

describe("srcToOut", () => {
  it("maps kept source time to output time", () => {
    expect(srcToOut(TL, 2)).toBeCloseTo(2);
    expect(srcToOut(TL, 8)).toBeCloseTo(4);
  });
  it("returns null inside a removed span", () => {
    expect(srcToOut(TL, 4)).toBeNull();
    expect(srcToOut(TL, 5)).toBeNull();
  });
  it("respects speed", () => {
    expect(srcToOut(SPEED, 4)).toBeCloseTo(2);
  });
});

describe("outToSrc", () => {
  it("maps output time back to source", () => {
    expect(outToSrc(TL, 2)).toBeCloseTo(2);
    expect(outToSrc(TL, 4)).toBeCloseTo(8);
  });
  it("clamps at the ends", () => {
    expect(outToSrc(TL, -5)).toBe(0);
    expect(outToSrc(TL, 99)).toBe(10);
  });
  it("respects speed", () => {
    expect(outToSrc(SPEED, 2)).toBeCloseTo(4);
  });
});

describe("nextKeptSrc", () => {
  it("returns the same time inside a kept span", () => {
    expect(nextKeptSrc(TL, 2)).toBe(2);
  });
  it("returns the next segment start inside a removed span", () => {
    expect(nextKeptSrc(TL, 4)).toBe(7);
  });
  it("returns null past the last kept point", () => {
    const tl: TimelineData = {
      source_duration: 10,
      segments: [{ src_start: 0, src_end: 5, out_start: 0, out_end: 5, speed: 1 }],
      removed: [[5, 10]],
    };
    expect(nextKeptSrc(tl, 9)).toBeNull();
  });
});

describe("removedSpans", () => {
  it("lists the cut gaps", () => {
    expect(removedSpans(TL)).toEqual([[3, 7]]);
  });
});

describe("needsTranslation", () => {
  // A preview job's timeline is region-local: identity over the slice
  // length. srcOffset (its start_s) must still count as a translation —
  // otherwise A/B switching and the video mirror run on source time
  // against a slice that starts at 0 (R2 #13b).
  const PREVIEW_IDENTITY: TimelineData = {
    source_duration: 20,
    segments: [
      { src_start: 0, src_end: 20, out_start: 0, out_end: 20, speed: 1 },
    ],
    removed: [],
  };

  it("is true for a preview-region timeline even with no cuts", () => {
    expect(needsTranslation(PREVIEW_IDENTITY, 30)).toBe(true);
  });

  it("is false for a full-file identity timeline", () => {
    expect(needsTranslation(PREVIEW_IDENTITY, 0)).toBe(false);
  });

  it("is true for any cut map regardless of offset", () => {
    expect(needsTranslation(TL, 0)).toBe(true);
    expect(needsTranslation(TL, 30)).toBe(true);
  });

  it("is false without a timeline", () => {
    expect(needsTranslation(null, 30)).toBe(false);
  });
});
