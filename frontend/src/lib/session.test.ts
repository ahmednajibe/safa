import { describe, expect, it } from "vitest";
import { pickHydrationJob } from "./session";
import type { Job } from "../api/types";

const job = (id: string, mediaId: string): Job =>
  ({ id, media_id: mediaId }) as Job;

describe("pickHydrationJob", () => {
  const jobs = [job("j-new", "m1"), job("j-old", "m2"), job("j-older", "m1")];

  it("adopts nothing when no media was restored", () => {
    expect(pickHydrationJob(jobs, null)).toBeNull();
    expect(pickHydrationJob(jobs, "")).toBeNull();
  });

  it("picks the newest job for the restored media only", () => {
    expect(pickHydrationJob(jobs, "m1")?.id).toBe("j-new");
    expect(pickHydrationJob(jobs, "m2")?.id).toBe("j-old");
  });

  it("ignores jobs of other files", () => {
    expect(pickHydrationJob(jobs, "m3")).toBeNull();
  });
});
