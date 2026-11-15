import { useState } from "react";
import { useT } from "../i18n";
import { activePresetId } from "../state/reducers";
import { useStore } from "../state/store";
import type { OpSpec, Preset } from "../api/types";

/** Preset chips: backend order, active chip is the preset whose ops exactly
 *  match, else Custom. Applying scrolls the rack to the first enabled card. */
export function PresetBar({
  ops,
  presets,
  loaded,
}: {
  ops: OpSpec[];
  presets: Record<string, Preset>;
  loaded: boolean;
}) {
  const t = useT();
  const { media, selection, dispatchSelection } = useStore();
  const [partial, setPartial] = useState<string | null>(null);
  const disabled = media.status !== "ready" || !loaded;

  const PRESET_ORDER = [
    "podcast",
    "remove_music",
    "echoey_room",
    "lecture",
    "karaoke",
    "jump_cut",
  ];
  const ids = [
    ...PRESET_ORDER.filter((id) => id in presets),
    ...Object.keys(presets).filter((id) => !PRESET_ORDER.includes(id)),
  ];
  const active = activePresetId(presets, selection.ops);

  const apply = (id: string) => {
    const p = presets[id];
    if (!p) return;
    // Drop ops that aren't available on this machine — preset still applies.
    const dropped = Object.keys(p.ops).filter(
      (opId) => !ops.find((o) => o.id === opId)?.available,
    );
    const usable: Record<string, Record<string, unknown>> = {};
    for (const [opId, params] of Object.entries(p.ops)) {
      if (!dropped.includes(opId)) usable[opId] = params;
    }
    dispatchSelection({ type: "apply-preset", presetId: id, ops: usable });
    setPartial(dropped.length ? id : null);
    // Scroll the rack to the first enabled card.
    requestAnimationFrame(() => {
      const first = Object.keys(usable)[0];
      if (first) {
        document
          .querySelector(`[data-op-id="${first}"]`)
          ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    });
  };

  const presetLabel = (id: string): string => {
    const dict = t.d.preset as Record<string, string>;
    return dict[id] ?? presets[id]?.name ?? id;
  };

  return (
    <div className="preset-bar" role="group" aria-label="Presets">
      {ids.map((id) => (
        <button
          key={id}
          type="button"
          className={`chip-btn${active === id ? " on" : ""}`}
          disabled={disabled}
          aria-pressed={active === id}
          onClick={() => apply(id)}
        >
          {presetLabel(id)}
        </button>
      ))}
      <span
        className={`chip-btn static${active === null && Object.keys(selection.ops).length ? " on" : ""}${disabled ? " dim" : ""}`}
        role="status"
      >
        {t.d.preset.custom}
      </span>
      <button
        type="button"
        className="chip-btn"
        disabled={disabled || !Object.keys(selection.ops).length}
        onClick={() => {
          dispatchSelection({ type: "off" });
          setPartial(null);
        }}
      >
        {t.d.preset.turnOff}
      </button>
      {partial && (
        <span className="preset-note" role="status">
          {t.t(t.d.preset.partial, { name: presetLabel(partial) })}
        </span>
      )}
    </div>
  );
}
