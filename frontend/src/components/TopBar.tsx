import type { Health } from "../api/types";

function shortGpuName(name: string): string {
  return name.replace(/^NVIDIA\s+(GeForce\s+)?/i, "").trim() || name;
}

function Mark() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      aria-hidden="true"
      className="shrink-0"
    >
      <rect x="1.5" y="1.5" width="17" height="17" rx="4" stroke="#fff" strokeWidth="1.6" />
      <path
        d="M6 8.5v3M10 5.5v9M14 8v4"
        stroke="#fff"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function TopBar({ health }: { health: Health | null }) {
  const hardware =
    health === null
      ? null
      : health.device === "gpu" && health.gpu
        ? `${shortGpuName(health.gpu.name)} · GPU ready`
        : "CPU only";

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 px-4 lg:px-3.5">
      <Mark />
      <span className="whitespace-nowrap text-section font-semibold">
        Sound System
      </span>
      <div className="min-w-0 flex-1" />
      {hardware && (
        <span className="pill max-w-[45vw] truncate tabular-nums">
          {hardware}
        </span>
      )}
      <button type="button" className="btn-ghost">
        History
      </button>
    </header>
  );
}
