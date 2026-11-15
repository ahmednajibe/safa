import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Job } from "../api/types";
import { I18nProvider, makeI18n } from "../i18n";
import { emptyJob } from "../state/reducers";
import { StoreProvider } from "../state/store";
import { Dock } from "./Dock";

const RUNNING: Job = {
  id: "b".repeat(32),
  media_id: "a".repeat(32),
  kind: "full",
  ops: [{ id: "silence", params: {} }],
  output: { audio_format: "wav", keep_video: false },
  preview: null,
  requested_device: "cpu",
  audio_stream: 0,
  state: "running",
  stage: "Cut silence",
  stage_id: "silence",
  stage_idx: 9,
  total_stages: 11,
  stage_pct: 0.9,
  overall_pct: 0.92,
  eta_sec: 3,
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
  files: [],
};

const DONE_AUTOSAVE_FAILED: Job = {
  ...RUNNING,
  state: "completed",
  stage: "Done",
  stage_id: "done",
  stage_idx: 10,
  total_stages: 11,
  stage_pct: 1,
  overall_pct: 1,
  message_code: "job_done",
  warnings: [
    { warning_code: "auto_save_failed", warning_params: { path: "D:\\out" } },
    { warning_code: "saved_to", warning_params: { path: "D:\\out\\o.wav" } },
  ],
  files: [
    {
      name: "out.wav",
      size_bytes: 1234,
      kind: "audio",
      saved_path: null,
    },
  ],
};

function renderDock(j: Job) {
  return render(
    <StoreProvider
      initial={{ job: { ...emptyJob, activeId: j.id, job: j } }}
    >
      <I18nProvider value={makeI18n("en")}>
        <Dock
          estimate={null}
          gpuName={null}
          mediaReady
          hasOps
          compact={false}
          onPreview={() => {}}
          onProcess={() => {}}
        />
      </I18nProvider>
    </StoreProvider>,
  );
}

afterEach(cleanup);

describe("running progress bar", () => {
  it("fill width uses overall_pct (0..1 → 0..100%), not stage_pct", () => {
    const { container } = renderDock(RUNNING);
    const fill = container.querySelector<HTMLElement>(".progress-fill");
    expect(fill).toBeTruthy();
    const width = parseFloat(fill!.style.inlineSize);
    // overall_pct 0.92 → ~92%; at step 10/11 the bar must pass (n-1)/N
    expect(width).toBeGreaterThan((9 / 11) * 100);
    expect(width).toBeGreaterThan(85);
    expect(width).toBeLessThanOrEqual(100);
  });

  it("bar aria-valuenow is the overall percent", () => {
    const { container } = renderDock(RUNNING);
    const bar = container.querySelector(".progress");
    expect(bar?.getAttribute("aria-valuenow")).toBe("92");
  });

  it("text shows the step counter and the stage percent", () => {
    const { container } = renderDock(RUNNING);
    const text = container.querySelector(".run-meta")?.textContent ?? "";
    expect(text).toContain("Step 10 of 11");
    expect(text).toContain("90%"); // stage_pct 0.9 → 90%, not overall 92%
  });
});

describe("done/save area", () => {
  it("shows auto_save_failed as a save-area warning", () => {
    const { container } = renderDock(DONE_AUTOSAVE_FAILED);
    const saveArea = container.querySelector(".job-result");
    expect(saveArea?.textContent).toContain(
      "Couldn't save to D:\\out. Save it manually.",
    );
  });
});
