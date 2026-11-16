import { describe, expect, it, vi } from "vitest";
import type { Job, MediaInfo } from "./api/types";
import { ApiError } from "./api/types";
import { adoptJob } from "./actions";
import { emptyMedia, emptyJob, type MediaState } from "./state/reducers";
import type { Store } from "./state/store";

const READY: MediaInfo = {
  media_id: "b".repeat(32),
  filename: "other.wav",
  size_bytes: 10,
  probe: {
    duration_s: 3,
    format_name: "wav",
    size_bytes: 10,
    audio: [],
    video: [],
    has_video: false,
  },
  lufs: -18,
  ready: { peaks: true, playback: true, error: null },
};

function jobFor(mediaId: string): Job {
  return {
    id: "c".repeat(32),
    media_id: mediaId,
    kind: "full",
    ops: [],
    output: { audio_format: "wav", keep_video: false },
    preview: null,
    requested_device: "cpu",
    audio_stream: 0,
    state: "completed",
    stage: "Done",
    stage_id: "done",
    stage_idx: 1,
    total_stages: 1,
    stage_pct: 1,
    overall_pct: 1,
    eta_sec: 0,
    device: "cpu",
    message: null,
    error: null,
    message_code: null,
    message_params: {},
    error_code: null,
    error_params: {},
    warnings: [],
    stages: [],
    created_at: "2026-11-15T00:00:00Z",
    started_at: null,
    finished_at: null,
    retry_of: null,
    queue_position: null,
    keep: false,
    files: [],
  };
}

function fakeStore(media: MediaState) {
  return {
    media,
    job: { ...emptyJob },
    dispatchMedia: vi.fn(),
    dispatchJob: vi.fn(),
  } as unknown as Store & {
    dispatchMedia: ReturnType<typeof vi.fn>;
    dispatchJob: ReturnType<typeof vi.fn>;
  };
}

const loaded: MediaState = {
  ...emptyMedia,
  status: "ready",
  mediaId: "a".repeat(32),
  filename: "current.wav",
};

describe("adoptJob (R2 #13c)", () => {
  it("adopts a same-media job without touching media", async () => {
    const store = fakeStore(loaded);
    const j = jobFor("a".repeat(32));
    const fetcher = vi.fn();
    await adoptJob(store, j, fetcher);
    expect(fetcher).not.toHaveBeenCalled();
    expect(store.dispatchMedia).not.toHaveBeenCalled();
    expect(store.dispatchJob).toHaveBeenCalledWith({
      type: "set-active",
      job: j,
    });
  });

  it("loads the job's media before adopting a foreign job", async () => {
    const fetcher = vi.fn().mockResolvedValue(READY);
    const store = fakeStore(loaded);
    const j = jobFor("b".repeat(32));
    await adoptJob(store, j, fetcher);
    expect(fetcher).toHaveBeenCalledWith("b".repeat(32));
    expect(store.dispatchMedia).toHaveBeenCalledWith({
      type: "media-ready",
      media: READY,
    });
    expect(store.dispatchJob).toHaveBeenCalledWith({
      type: "set-active",
      job: j,
    });
  });

  it("still adopts when the job's media is still preparing", async () => {
    const preparing: MediaInfo = {
      ...READY,
      lufs: null,
      ready: { peaks: false, playback: false, error: null },
    };
    const fetcher = vi.fn().mockResolvedValue(preparing);
    const store = fakeStore(loaded);
    const j = jobFor("b".repeat(32));
    await adoptJob(store, j, fetcher);
    expect(store.dispatchMedia).toHaveBeenCalledWith({
      type: "uploaded",
      media: preparing,
    });
    expect(store.dispatchJob).toHaveBeenCalledWith({
      type: "set-active",
      job: j,
    });
  });

  it("clears media instead of pairing output with another file", async () => {
    // NOTE: injected fetcher, not a module mock — vitest 4's mocker reports
    // ANY rejection crossing a mocked-module boundary as a phantom test error
    // even when adoptJob's catch handles it.
    const fetcher = vi
      .fn()
      .mockRejectedValue(new ApiError("media_not_found", "gone"));
    const store = fakeStore(loaded);
    const j = jobFor("b".repeat(32));
    await adoptJob(store, j, fetcher);
    // The original is gone — never pair j's output with loaded media.
    expect(store.dispatchMedia).toHaveBeenCalledWith({ type: "clear" });
    expect(store.dispatchJob).toHaveBeenCalledWith({
      type: "set-active",
      job: j,
    });
  });
});
