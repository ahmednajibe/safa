import { describe, expect, it } from "vitest";
import {
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
