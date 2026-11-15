import type { KeyboardEvent } from "react";

/** Toggle switch — role=switch, 36×20, white track + black knob when on.
 *  The knob slides along the inline axis (RTL-safe: inset-inline-start). */
export function Switch({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      if (!disabled) onChange(!checked);
    }
  };
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className="switch"
      onClick={() => !disabled && onChange(!checked)}
      onKeyDown={onKey}
    />
  );
}
