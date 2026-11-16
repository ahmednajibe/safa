import { describe, expect, it } from "vitest";
import { shortGpuName, truncateMiddle } from "./format";

describe("shortGpuName", () => {
  it("strips vendor prefixes", () => {
    expect(shortGpuName("NVIDIA GeForce GTX 1650")).toBe("GTX 1650");
    expect(shortGpuName("NVIDIA GTX 1650")).toBe("GTX 1650");
    expect(shortGpuName("NVIDIA GeForce RTX 4070 Ti")).toBe("RTX 4070 Ti");
  });

  it("keeps non-NVIDIA and already-short names", () => {
    expect(shortGpuName("AMD Radeon RX 6600")).toBe("AMD Radeon RX 6600");
    expect(shortGpuName("GTX 1650")).toBe("GTX 1650");
  });
});

describe("truncateMiddle", () => {
  it("keeps short paths whole", () => {
    expect(truncateMiddle("D:\\out\\a.wav", 42)).toBe("D:\\out\\a.wav");
  });

  it("middle-truncates long paths with an ellipsis", () => {
    const p = "C:\\Users\\user\\AppData\\Local\\Temp\\ss-e2e-8n3f1nwb\\autosave\\file.mp4";
    const s = truncateMiddle(p, 42);
    expect(s.length).toBeLessThanOrEqual(43);
    expect(s).toContain("…");
    expect(s.startsWith("C:\\Users\\user")).toBe(true);
    expect(s.endsWith("file.mp4")).toBe(true);
  });
});
