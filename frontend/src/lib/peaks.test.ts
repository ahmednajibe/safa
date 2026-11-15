import { describe, expect, it } from "vitest";
import { flattenPeaks, hatchSpans, rulerTicks } from "./peaks";
import type { TimelineData } from "./timeline";

const TL: TimelineData = {
  source_duration: 10,
  segments: [
    { src_start: 1, src_end: 3, out_start: 0, out_end: 2, speed: 1 },
    { src_start: 7, src_end: 9, out_start: 2, out_end: 4, speed: 1 },
  ],
  removed: [
    [0, 1],
    [3, 7],
    [9, 10],
  ],
};

describe("flattenPeaks", () => {
  it("flattens [min,max] pairs into per-channel samples, capped", () => {
    const data = [
      [
        [-0.5, 0.6],
        [-0.2, 0.3],
      ],
    ];
    expect(flattenPeaks(data)).toEqual([[-0.5, 0.6, -0.2, 0.3]]);
  });
  it("decimates when pairs exceed the cap", () => {
    const pairs: number[][] = [];
    for (let i = 0; i < 1000; i++) pairs.push([-0.01 * i, 0.01 * i]);
    const out = flattenPeaks([pairs], 500);
    expect(out[0].length).toBeLessThanOrEqual(1000);
    expect(out[0][0]).toBeCloseTo(-0.01, 5); // first group's min folded
    // global max must survive decimation
    expect(Math.max(...out[0].map(Math.abs))).toBeCloseTo(9.99, 1);
  });
});

describe("hatchSpans (timeline → cut overlay)", () => {
  it("full job: removed spans incl. head/tail", () => {
    expect(hatchSpans(TL, 0)).toEqual([
      [0, 1],
      [3, 7],
      [9, 10],
    ]);
  });
  it("preview job (srcOffset>0): every local removal, shifted", () => {
    // preview covered src 10..20 of a 30 s file; the local domain's
    // removed spans — including local head/tail — are real cuts inside
    // the slice. Outside 10..20 is "not covered", not removed.
    const preview: TimelineData = {
      source_duration: 10,
      segments: [
        { src_start: 2, src_end: 3, out_start: 0, out_end: 1, speed: 1 },
        { src_start: 7, src_end: 10, out_start: 1, out_end: 4, speed: 1 },
      ],
      removed: [
        [0, 2],
        [3, 7],
      ],
    };
    expect(hatchSpans(preview, 10)).toEqual([
      [10, 12],
      [13, 17],
    ]);
  });
  it("identity timeline → no spans", () => {
    const id: TimelineData = {
      source_duration: 10,
      segments: [{ src_start: 0, src_end: 10, out_start: 0, out_end: 10, speed: 1 }],
      removed: [],
    };
    expect(hatchSpans(id, 0)).toEqual([]);
  });
});

describe("rulerTicks", () => {
  it("picks a step giving ~80 px spacing at any zoom", () => {
    // 60 s at 10 px/s → step should land ~10 s
    const ticks = rulerTicks(60, 10);
    expect(ticks.length).toBeGreaterThanOrEqual(4);
    expect(ticks.length).toBeLessThanOrEqual(9);
    const step = ticks[1] - ticks[0];
    expect(step).toBe(10);
  });
  it("zoomed in: smaller steps", () => {
    const ticks = rulerTicks(60, 100);
    const step = ticks[1] - ticks[0];
    expect(step).toBe(1);
  });
  it("long file at fit zoom: coarse steps", () => {
    // 3600 s at 0.17 px/s → step ~600 s
    const ticks = rulerTicks(3600, 0.17);
    const step = ticks[1] - ticks[0];
    expect(step).toBeGreaterThanOrEqual(300);
  });
});
