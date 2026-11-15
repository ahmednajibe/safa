import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { SegmentedControl } from "./SegmentedControl";

const OPTS = [
  { value: "a", label: "Alpha" },
  { value: "b", label: "Beta" },
  { value: "c", label: "Gamma" },
];

function Demo() {
  const [v, setV] = useState("a");
  return (
    <SegmentedControl label="Pick" options={OPTS} value={v} onChange={setV} />
  );
}

afterEach(() => {
  cleanup();
  document.documentElement.dir = "ltr";
  document.documentElement.lang = "en";
});

describe("SegmentedControl", () => {
  it("is a radiogroup of radios with roving tabindex", () => {
    render(<Demo />);
    const group = screen.getByRole("radiogroup", { name: "Pick" });
    const radios = screen.getAllByRole("radio");
    expect(group).toBeTruthy();
    expect(radios).toHaveLength(3);
    expect(radios[0].getAttribute("aria-checked")).toBe("true");
    expect(radios[0].tabIndex).toBe(0);
    expect(radios[1].tabIndex).toBe(-1);
  });

  it("ArrowRight advances selection in LTR", () => {
    render(<Demo />);
    const radios = screen.getAllByRole("radio");
    radios[0].focus();
    fireEvent.keyDown(radios[0], { key: "ArrowRight" });
    expect(radios[1].getAttribute("aria-checked")).toBe("true");
  });

  it("ArrowLeft advances selection in RTL (visual order)", () => {
    document.documentElement.dir = "rtl";
    render(<Demo />);
    const radios = screen.getAllByRole("radio");
    radios[0].focus();
    fireEvent.keyDown(radios[0], { key: "ArrowLeft" });
    expect(radios[1].getAttribute("aria-checked")).toBe("true");
  });

  it("ArrowRight goes back in RTL; Home/End jump", () => {
    document.documentElement.dir = "rtl";
    const onChange = vi.fn();
    render(
      <SegmentedControl
        label="Pick"
        options={OPTS}
        value="b"
        onChange={onChange}
      />
    );
    const radios = screen.getAllByRole("radio");
    fireEvent.keyDown(radios[1], { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("a");
    fireEvent.keyDown(radios[1], { key: "End" });
    expect(onChange).toHaveBeenCalledWith("c");
    fireEvent.keyDown(radios[1], { key: "Home" });
    expect(onChange).toHaveBeenCalledWith("a");
  });

  it("clicks select", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        label="Pick"
        options={OPTS}
        value="a"
        onChange={onChange}
      />
    );
    fireEvent.click(screen.getByRole("radio", { name: "Gamma" }));
    expect(onChange).toHaveBeenCalledWith("c");
  });

  it("skips disabled options on arrow keys", () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl
        label="Pick"
        options={[OPTS[0], { ...OPTS[1], disabled: true }, OPTS[2]]}
        value="a"
        onChange={onChange}
      />
    );
    const radios = screen.getAllByRole("radio");
    fireEvent.keyDown(radios[0], { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("c");
  });
});
