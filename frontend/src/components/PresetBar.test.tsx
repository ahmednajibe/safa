import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { I18nProvider, makeI18n } from "../i18n";
import { emptySelection, type SelectionState } from "../state/reducers";
import { StoreProvider } from "../state/store";
import { PresetBar } from "./PresetBar";

const PRESETS = {
  podcast: { name: "Podcast cleanup", ops: { denoise: {} } },
};

function renderBar(selection?: SelectionState) {
  return render(
    <StoreProvider initial={selection ? { selection } : undefined}>
      <I18nProvider value={makeI18n("en")}>
        <PresetBar ops={[]} presets={PRESETS} loaded />
      </I18nProvider>
    </StoreProvider>,
  );
}

afterEach(cleanup);

describe("PresetBar Custom chip", () => {
  it("is not selected with 0 ops on", () => {
    renderBar();
    const custom = document.querySelector(".chip-btn.static");
    expect(custom).toBeTruthy();
    expect(custom?.classList.contains("on")).toBe(false);
    // And it doesn't pop brighter than its disabled siblings (no file yet).
    expect(custom?.classList.contains("dim")).toBe(true);
  });

  it("is selected when ops on match no preset", () => {
    renderBar({ ...emptySelection, ops: { trim: {} } });
    const custom = document.querySelector(".chip-btn.static");
    expect(custom?.classList.contains("on")).toBe(true);
  });

  it("is not selected when ops equal a preset", () => {
    renderBar({ ...emptySelection, ops: { denoise: {} } });
    const custom = document.querySelector(".chip-btn.static");
    expect(custom?.classList.contains("on")).toBe(false);
  });
});
