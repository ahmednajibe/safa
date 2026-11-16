import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Job } from "../api/types";
import { I18nProvider, makeI18n } from "../i18n";
import { emptyJob } from "../state/reducers";
import { StoreProvider } from "../state/store";
import { ResultsPanel } from "./ResultsPanel";

const JOB: Job = {
  id: "b".repeat(32),
  media_id: "a".repeat(32),
  kind: "full",
  ops: [{ id: "silence", params: {} }],
  output: { audio_format: "wav", keep_video: true },
  preview: null,
  requested_device: "gpu",
  audio_stream: 0,
  state: "completed",
  stage: "Done",
  stage_id: "done",
  stage_idx: 3,
  total_stages: 3,
  stage_pct: 1,
  overall_pct: 1,
  eta_sec: 0,
  device: "cpu",
  message: "Done",
  error: null,
  message_code: "job_done",
  message_params: {},
  error_code: null,
  error_params: {},
  warnings: [
    { warning_code: "auto_save_failed", warning_params: { path: "D:\\out" } },
  ],
  stages: [
    {
      id: "silence",
      name: "Cut silence",
      device: "cpu",
      actual_device: "cpu",
      fallback_reason: null,
      state: "done",
      seconds: 1.2,
      message_code: "silence_removed",
      message_params: { seconds: 2.9, count: 3 },
    },
    {
      id: "loudness",
      name: "Even out volume",
      device: "gpu",
      actual_device: "cpu",
      fallback_reason: "out of VRAM",
      state: "done",
      seconds: 4.52,
    },
  ],
  created_at: "2026-11-15T00:00:00Z",
  started_at: null,
  finished_at: null,
  retry_of: null,
  queue_position: null,
  keep: false,
  files: [],
};

function renderPanel(j: Job | null) {
  return render(
    <StoreProvider
      initial={{ job: { ...emptyJob, activeId: j?.id ?? null, job: j } }}
    >
      <I18nProvider value={makeI18n("en")}>
        <ResultsPanel />
      </I18nProvider>
    </StoreProvider>,
  );
}

afterEach(cleanup);

describe("ResultsPanel", () => {
  it("renders nothing before a job", () => {
    const { container } = renderPanel(null);
    expect(container.querySelector(".results")).toBeNull();
  });

  it("renders nothing while the job is running", () => {
    const { container } = renderPanel({ ...JOB, state: "running" });
    expect(container.querySelector(".results")).toBeNull();
  });

  it("lists stages in order with device pill, seconds and summary", () => {
    const { container } = renderPanel(JOB);
    expect(screen.getByText("Results")).toBeTruthy();
    const rows = container.querySelectorAll(".result-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("Cut silence");
    expect(rows[0].textContent).toContain(
      "Removed 2.9 s of silence in 3 places",
    );
    expect(rows[0].textContent).toContain("1.2 s");
    expect(rows[1].textContent).toContain("Even out volume");
    expect(rows[1].textContent).toContain("4.5 s");
    // actual_device (cpu fallback) wins over the planned gpu
    const pills = container.querySelectorAll(".result-row .pill");
    expect(pills[0].textContent).toBe("Processor");
    expect(pills[1].textContent).toBe("Processor");
  });

  it("keeps job-level save messages out of the stage list", () => {
    const { container } = renderPanel(JOB);
    // saved_to / auto_save_failed are job-level: they belong to the
    // done/save area, never under a stage row in Results.
    expect(container.textContent).not.toContain("Couldn't save to");
    expect(
      screen.queryByText(/Couldn't save to D:\\out\. Save it manually\./),
    ).toBeNull();
  });

  it("still shows non-save job warnings", () => {
    const j: Job = {
      ...JOB,
      warnings: [
        {
          warning_code: "preset_partial",
          warning_params: { skipped_op_ids: "noise" },
        },
      ],
    };
    renderPanel(j);
    expect(
      screen.getByText(/Some steps were skipped: noise/),
    ).toBeTruthy();
  });
});
