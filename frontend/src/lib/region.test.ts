import { describe, expect, it } from "vitest";
import { clampRegion, dragRegion, nudgeRegion } from "./region";

describe("clampRegion", () => {
  it("keeps a valid region inside the duration", () => {
    expect(clampRegion({ start_s: 2, end_s: 8 }, 30)).toEqual({
      start_s: 2,
      end_s: 8,
    });
  });
  it("clamps to [0, duration] and keeps min length", () => {
    expect(clampRegion({ start_s: -3, end_s: 8 }, 30).start_s).toBe(0);
    expect(clampRegion({ start_s: 2, end_s: 99 }, 30).end_s).toBe(30);
    const r = clampRegion({ start_s: 29.7, end_s: 29.9 }, 30);
    expect(r.end_s - r.start_s).toBeGreaterThanOrEqual(0.5);
  });
});

describe("dragRegion (edge + body moves)", () => {
  it("start edge clamps below end - minLen", () => {
    const r = dragRegion({ start_s: 5, end_s: 10 }, "start", 9.8, 30);
    expect(r.start_s).toBe(9.5); // end - 0.5
  });
  it("end edge clamps above start + minLen", () => {
    const r = dragRegion({ start_s: 5, end_s: 10 }, "end", 5.2, 30);
    expect(r.end_s).toBe(5.5);
  });
  it("body move shifts both and clamps at the edges", () => {
    expect(dragRegion({ start_s: 5, end_s: 10 }, "body", -10, 30)).toEqual({
      start_s: 0,
      end_s: 5,
    });
    expect(dragRegion({ start_s: 5, end_s: 10 }, "body", 40, 30)).toEqual({
      start_s: 25,
      end_s: 30,
    });
  });
});

describe("nudgeRegion ([ / ] = ±0.5 s)", () => {
  it("shifts the whole region", () => {
    expect(nudgeRegion({ start_s: 5, end_s: 10 }, -0.5, 30)).toEqual({
      start_s: 4.5,
      end_s: 9.5,
    });
    expect(nudgeRegion({ start_s: 5, end_s: 10 }, 0.5, 30)).toEqual({
      start_s: 5.5,
      end_s: 10.5,
    });
  });
  it("clamps at 0 and duration without shrinking", () => {
    expect(nudgeRegion({ start_s: 0.2, end_s: 5 }, -0.5, 30)).toEqual({
      start_s: 0,
      end_s: 4.8,
    });
    expect(nudgeRegion({ start_s: 25, end_s: 30 }, 0.5, 30)).toEqual({
      start_s: 25,
      end_s: 30,
    });
  });
});
