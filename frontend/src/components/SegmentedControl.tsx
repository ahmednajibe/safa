import { useRef, type KeyboardEvent } from "react";

export interface SegOption {
  value: string;
  label: string;
  disabled?: boolean;
}

/** Labelled radio group (UI_DESIGN §6): roving tabindex, arrows move in
 *  *visual* order — computed from document.dir so ArrowLeft advances in
 *  RTL with no DOM or CSS reversal. */
export function SegmentedControl({
  label,
  options,
  value,
  onChange,
  small = false,
}: {
  label: string;
  options: SegOption[];
  value: string;
  onChange: (v: string) => void;
  small?: boolean;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const enabledIdx = options
    .map((o, i) => (o.disabled ? -1 : i))
    .filter((i) => i >= 0);
  const selIdx = options.findIndex((o) => o.value === value);
  // roving tabindex lands on the selected option, else first enabled
  const curIdx =
    selIdx >= 0 && !options[selIdx].disabled
      ? selIdx
      : (enabledIdx[0] ?? -1);

  const move = (dir: 1 | -1) => {
    const pos = enabledIdx.indexOf(curIdx);
    const next = enabledIdx[(pos + dir + enabledIdx.length) % enabledIdx.length];
    if (next === undefined) return;
    onChange(options[next].value);
    refs.current[next]?.focus();
  };

  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    const rtl = document.documentElement.dir === "rtl";
    // Forward means "advance to the next option in DOM order": → in LTR,
    // ← in RTL (visual order matches DOM order in both directions).
    const forward = rtl ? "ArrowLeft" : "ArrowRight";
    const back = rtl ? "ArrowRight" : "ArrowLeft";
    if (e.key === forward) {
      e.preventDefault();
      move(1);
    } else if (e.key === back) {
      e.preventDefault();
      move(-1);
    } else if (e.key === "Home") {
      e.preventDefault();
      const first = enabledIdx[0];
      if (first !== undefined) {
        onChange(options[first].value);
        refs.current[first]?.focus();
      }
    } else if (e.key === "End") {
      e.preventDefault();
      const last = enabledIdx[enabledIdx.length - 1];
      if (last !== undefined) {
        onChange(options[last].value);
        refs.current[last]?.focus();
      }
    }
  };

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={`seg${small ? " seg-small" : ""}`}
    >
      {options.map((o, i) => (
        <button
          key={o.value}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          aria-disabled={o.disabled || undefined}
          tabIndex={o.disabled ? -1 : i === curIdx ? 0 : -1}
          className="seg-opt"
          onClick={() => !o.disabled && onChange(o.value)}
          onKeyDown={onKey}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
