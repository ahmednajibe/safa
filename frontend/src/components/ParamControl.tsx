import type { ParamSpec } from "../api/types";
import { useT } from "../i18n";
import { SegmentedControl } from "./SegmentedControl";
import { Switch } from "./Switch";

/** One schema-driven parameter control (§6): ≤3 options → segmented,
 *  more → select, number → slider+field+unit, bool → switch. */
export function ParamControl({
  opId,
  spec,
  value,
  onChange,
}: {
  opId: string;
  spec: ParamSpec;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const t = useT();
  const opText = t.d.op[opId as keyof typeof t.d.op];
  const label = opText?.params[spec.key] ?? spec.label;
  const cur = value === undefined ? spec.default : value;
  const id = `p-${opId}-${spec.key}`;

  if (spec.type === "bool") {
    return (
      <div className="param param-bool">
        <span className="param-label" id={`${id}-l`}>
          {label}
        </span>
        <Switch
          checked={Boolean(cur)}
          onChange={onChange}
          label={label}
        />
      </div>
    );
  }

  if (spec.type === "select" && spec.options) {
    const optLabel = (v: string | number): string => {
      const loc = opText?.options?.[spec.key]?.[String(v)];
      return (
        loc ?? spec.options?.find((o) => o.value === v)?.label ?? String(v)
      );
    };
    if (spec.options.length <= 3) {
      return (
        <div className="param">
          <SegmentedControl
            label={label}
            small
            options={spec.options.map((o) => ({
              value: String(o.value),
              label: optLabel(o.value),
            }))}
            value={String(cur)}
            onChange={(v) => {
              const orig = spec.options!.find((o) => String(o.value) === v);
              onChange(orig ? orig.value : v);
            }}
          />
        </div>
      );
    }
    return (
      <div className="param">
        <label className="param-label" htmlFor={id}>
          {label}
        </label>
        <select
          id={id}
          className="select"
          value={String(cur)}
          onChange={(e) => {
            const orig = spec.options!.find(
              (o) => String(o.value) === e.target.value,
            );
            onChange(orig ? orig.value : e.target.value);
          }}
        >
          {spec.options.map((o) => (
            <option key={String(o.value)} value={String(o.value)}>
              {optLabel(o.value)}
            </option>
          ))}
        </select>
      </div>
    );
  }

  // number: slider + field + unit
  const num = typeof cur === "number" ? cur : Number(cur) || 0;
  const step = spec.step ?? 1;
  return (
    <div className="param param-num">
      <label className="param-label" htmlFor={id}>
        {label}
      </label>
      <div className="num-row">
        <input
          type="range"
          className="slider"
          min={spec.min ?? 0}
          max={spec.max ?? 100}
          step={step}
          value={num}
          aria-label={label}
          dir="ltr"
          onChange={(e) => onChange(Number(e.target.value))}
        />
        <input
          id={id}
          type="number"
          className="num-field"
          min={spec.min ?? undefined}
          max={spec.max ?? undefined}
          step={step}
          value={num}
          dir="ltr"
          onChange={(e) => {
            const v = Number(e.target.value);
            if (Number.isFinite(v)) onChange(v);
          }}
        />
        {spec.unit && <span className="unit">{spec.unit}</span>}
      </div>
    </div>
  );
}
