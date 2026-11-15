import type { Health, OpSpec, Preset } from "../api/types";

const UNAVAILABLE_TEXT: Record<string, string> = {
  planned: "Coming later",
  module_missing: "Not available on this computer",
  model_missing: "Model not downloaded yet — connect to the internet once",
};

function deviceTag(op: OpSpec, health: Health | null): string {
  if (op.device === "gpu-optional") {
    return health?.device === "gpu" ? "GPU" : "CPU";
  }
  return op.device === "gpu" ? "GPU" : "CPU";
}

function Switch({ label }: { label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked="false"
      disabled
      className="switch"
      aria-label={label}
    />
  );
}

function OpRow({ op, health }: { op: OpSpec; health: Health | null }) {
  return (
    <li className="group relative flex items-center gap-3 py-2 min-h-[52px]">
      {/* spine segment: 1px --rule joining the cards, 12px in from the card
          edge, running behind the switch (UI_DESIGN §6) */}
      <span
        aria-hidden="true"
        className="absolute bottom-0 left-[12px] top-0 w-px bg-rule group-first:top-1/2 group-last:bottom-1/2"
      />
      <Switch label={op.name} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="min-w-0 truncate text-body font-semibold leading-tight">
            {op.name}
          </span>
          <span className="pill ml-auto shrink-0">{deviceTag(op, health)}</span>
        </div>
        <p className="mt-0.5 line-clamp-2 text-small leading-snug text-faint">
          {op.description}
        </p>
        {!op.available && op.unavailable_code && (
          <div className="mt-1.5">
            <span className="pill h-auto min-h-6 max-w-full whitespace-normal py-0.5 leading-snug">
              {UNAVAILABLE_TEXT[op.unavailable_code] ?? "Not available"}
            </span>
          </div>
        )}
      </div>
    </li>
  );
}

export default function OpsRack({
  ops,
  presets,
  health,
}: {
  ops: OpSpec[];
  presets: Record<string, Preset>;
  health: Health | null;
}) {
  return (
    <section
      aria-label="Steps"
      className="flex h-full min-h-0 flex-col rounded-panel bg-surface p-[22px]"
    >
      <div className="flex flex-wrap gap-2" aria-label="Presets">
        {Object.entries(presets).map(([key, preset]) => (
          <button key={key} type="button" className="chip" aria-pressed="false" disabled>
            {preset.name}
          </button>
        ))}
      </div>
      <div className="mt-4 flex items-center justify-between">
        <span className="text-small text-faint">Steps run top to bottom</span>
        <span className="text-small text-faint">Add a file to start</span>
      </div>
      <ol className="relative mt-2 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
        {ops.map((op) => (
          <OpRow key={op.id} op={op} health={health} />
        ))}
      </ol>
    </section>
  );
}
