import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../api/types";
import { ApiError } from "../api/types";
import { I18nProvider, makeI18n } from "../i18n";
import { StoreProvider } from "../state/store";

// v0.1.1: "Save again" goes through the local server's native Save As
// window (saveJobFileOnServer); downloadJobFile is the no-GUI fallback.
// Everything else in ../api/client stays real (getJob & co. are unused
// while the job isn't the active one).
const saveMock = vi.fn();
const downloadMock = vi.fn();
vi.mock("../api/client", async () => {
  const real =
    await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...real,
    saveJobFileOnServer: (...args: unknown[]) => saveMock(...args),
  };
});
vi.mock("../lib/save", () => ({
  downloadJobFile: (...args: unknown[]) => downloadMock(...args),
}));

import { HistoryDrawer } from "./HistoryDrawer";

const DONE: Job = {
  id: "c".repeat(32),
  media_id: "a".repeat(32),
  kind: "full",
  ops: [{ id: "loudness", params: {} }],
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
  message_code: "job_done",
  message_params: {},
  error_code: null,
  error_params: {},
  warnings: [],
  stages: [],
  created_at: "2026-11-17T00:00:00Z",
  started_at: null,
  finished_at: "2026-11-17T00:01:00Z",
  retry_of: null,
  queue_position: null,
  keep: false,
  files: [
    { name: "song_clean.wav", size_bytes: 1000, kind: "audio", saved_path: null },
  ],
};

function Harness({ open }: { open: boolean }) {
  return open ? (
    <HistoryDrawer jobs={[DONE]} refresh={() => {}} onClose={() => {}} />
  ) : null;
}

function renderDrawer(open = true) {
  const ui = (o: boolean) => (
    <StoreProvider>
      <I18nProvider value={makeI18n("en")}>
        <Harness open={o} />
      </I18nProvider>
    </StoreProvider>
  );
  const r = render(ui(open));
  return { ...r, setOpen: (o: boolean) => r.rerender(ui(o)) };
}

afterEach(() => {
  cleanup();
  saveMock.mockReset();
  downloadMock.mockReset();
});

describe("HistoryDrawer save again (server save-as)", () => {
  it("shows 'Choose where to save…' while the request is pending", async () => {
    let finish: (v: { status: string }) => void = () => {};
    saveMock.mockImplementation(
      () => new Promise((resolve) => (finish = resolve)),
    );
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: /Save again/ }));
    expect((await screen.findByRole("status")).textContent).toContain(
      "Choose where to save",
    );
    await act(async () => finish({ status: "saved" }));
  });

  it("shows Saved after the server copies the file", async () => {
    saveMock.mockResolvedValue({ status: "saved", path: "D:\\out\\song_clean.wav" });
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: /Save again/ }));
    expect(await screen.findByText("Saved")).toBeTruthy();
  });

  it("shows the error text when the server save fails", async () => {
    saveMock.mockRejectedValue(
      new ApiError("save_failed", "Couldn't save the file. Try again."),
    );
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: /Save again/ }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Couldn't save the file",
    );
  });

  it("returns to the button when the dialog is cancelled", async () => {
    saveMock.mockResolvedValue({ status: "cancelled" });
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: /Save again/ }));
    await act(async () => {});
    expect(screen.getByRole("button", { name: /Save again/ })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a double click while the dialog is open starts exactly one save", async () => {
    saveMock.mockImplementation(() => new Promise(() => {})); // dialog stays open
    renderDrawer();
    const btn = screen.getByRole("button", { name: /Save again/ });
    fireEvent.click(btn);
    fireEvent.click(btn);
    await act(async () => {});
    expect(saveMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the Saved mark when the drawer is closed and reopened", async () => {
    let finish: (v: { status: string }) => void = () => {};
    saveMock.mockImplementation(
      () => new Promise((resolve) => (finish = resolve)),
    );
    const d = renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: /Save again/ }));
    await screen.findByRole("status");
    d.setOpen(false);
    await act(async () => finish({ status: "saved" }));
    d.setOpen(true);
    expect(screen.getByText("Saved")).toBeTruthy();
  });

  it("falls back to a download when the server has no GUI", async () => {
    saveMock.mockRejectedValue(
      new ApiError("picker_unavailable", "The folder window couldn't open."),
    );
    renderDrawer();
    fireEvent.click(screen.getByRole("button", { name: /Save again/ }));
    expect(await screen.findByText("Saved")).toBeTruthy();
    expect(downloadMock).toHaveBeenCalledWith(DONE.id, "song_clean.wav");
  });
});
