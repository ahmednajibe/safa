import { describe, expect, it } from "vitest";
import { ar } from "./ar";
import { en } from "./en";
import { formatMessage } from "./format";

function flatten(obj: unknown, prefix = ""): string[] {
  const out: string[] = [];
  if (obj !== null && typeof obj === "object") {
    for (const [k, v] of Object.entries(obj)) {
      out.push(...flatten(v, prefix ? `${prefix}.${k}` : k));
    }
  } else {
    out.push(prefix);
  }
  return out;
}

describe("dictionary parity", () => {
  it("ar has exactly the en key set (nested)", () => {
    expect(flatten(ar).sort()).toEqual(flatten(en).sort());
  });
});

describe("formatMessage plurals (ar)", () => {
  const code = "silence_removed";

  it("count 0 → zero form", () => {
    const s = formatMessage(code, { seconds: 5, count: 0 }, ar, null, "ar");
    expect(s).toContain("0 موضع");
  });
  it("count 1 → one form", () => {
    const s = formatMessage(code, { seconds: 5, count: 1 }, ar, null, "ar");
    expect(s).toContain("موضع واحد");
  });
  it("count 2 → two form", () => {
    const s = formatMessage(code, { seconds: 5, count: 2 }, ar, null, "ar");
    expect(s).toContain("موضعين");
  });
  it("count 5 → few form (3–10)", () => {
    const s = formatMessage(code, { seconds: 5, count: 5 }, ar, null, "ar");
    expect(s).toContain("5 مواضع");
  });
  it("count 15 → many form (11–99)", () => {
    const s = formatMessage(code, { seconds: 5, count: 15 }, ar, null, "ar");
    expect(s).toContain("15 موضعًا");
  });
  it("count 100 → other form (100+)", () => {
    const s = formatMessage(code, { seconds: 5, count: 100 }, ar, null, "ar");
    expect(s).toContain("100 موضع");
  });
});

describe("done_files message", () => {
  it("en: count-aware done line", () => {
    expect(formatMessage("done_files", { count: 1 }, en)).toBe("Done · 1 file");
    expect(formatMessage("done_files", { count: 3 }, en)).toBe("Done · 3 files");
  });
  it("ar: plural-aware done line", () => {
    expect(formatMessage("done_files", { count: 1 }, ar, null, "ar")).toContain(
      "ملف واحد",
    );
    expect(formatMessage("done_files", { count: 2 }, ar, null, "ar")).toContain(
      "ملفان",
    );
    expect(formatMessage("done_files", { count: 3 }, ar, null, "ar")).toContain(
      "3 ملفات",
    );
  });
});

describe("formatMessage basics", () => {
  it("interpolates params", () => {
    expect(
      formatMessage("job_queued", { position: 3 }, en)
    ).toBe("Queued — position 3");
  });
  it("unknown code falls back to the backend English text", () => {
    expect(
      formatMessage("some_new_code", {}, en, "Backend words here")
    ).toBe("Backend words here");
  });
  it("plain-plural message: breaths count picks a form", () => {
    const s = formatMessage("breaths_reduced", { count: 1, db: 8 }, ar, null, "ar");
    expect(s).toContain("نَفَس");
  });
});

describe("breaths_reduced count=0", () => {
  it("en: 'No breaths found' instead of 'Made 0 breaths quieter…'", () => {
    expect(
      formatMessage("breaths_reduced", { count: 0, db: 8 }, en)
    ).toBe("No breaths found");
  });
  it("ar: 'لم يُعثر على صوت تنفس'", () => {
    expect(
      formatMessage("breaths_reduced", { count: 0, db: 8 }, ar, null, "ar")
    ).toBe("لم يُعثر على صوت تنفس");
  });
  it("non-zero counts still use the normal template", () => {
    const s = formatMessage("breaths_reduced", { count: 4, db: 8 }, en);
    expect(s).toContain("4 breaths");
    expect(s).toContain("8 dB");
  });
});

describe("room_resonances_cut list formatting", () => {
  it("en: Intl.ListFormat joins the frequencies", () => {
    const s = formatMessage(
      "room_resonances_cut",
      { freqs: [281, 516, 621] },
      en,
    );
    expect(s).toContain("281");
    expect(s).toContain("516");
    expect(s).toContain("621");
    expect(s).toContain("and");
    expect(s).toMatch(/281, 516,? and 621 Hz/);
  });
  it("ar: Arabic list format, Western digits", () => {
    const s = formatMessage(
      "room_resonances_cut",
      { freqs: [281, 516, 621] },
      ar,
      null,
      "ar",
    );
    expect(s).toContain("281");
    expect(s).toContain("516");
    expect(s).toContain("621");
    expect(s).toContain("و"); // Arabic conjunction
    expect(s).not.toMatch(/[٠-٩]/); // no Arabic-Indic digits
  });
});
