/** TS mirrors of the /api/health and /api/operations payloads. */

export interface GpuInfo {
  name: string;
  total_mb: number;
  used_mb: number;
  driver: string;
}

export interface FfmpegInfo {
  path?: string;
  ffprobe_path?: string;
  version?: string;
  nvenc?: boolean;
  error?: string;
}

export interface Health {
  status: string;
  python: string;
  gpu: GpuInfo | null;
  device: "gpu" | "cpu";
  cuda_dlls: boolean;
  ffmpeg: FfmpegInfo;
  modules: Record<string, boolean>;
  models: Record<string, boolean>;
}

export interface ParamOption {
  value: string | number;
  label: string;
}

export interface ParamSpec {
  key: string;
  label: string;
  type: "number" | "select" | "bool";
  default: unknown;
  min: number | null;
  max: number | null;
  step: number | null;
  unit: string | null;
  options: ParamOption[] | null;
  advanced: boolean;
  help: string | null;
}

export interface OpSpec {
  id: string;
  order: number;
  name: string;
  description: string;
  engine: string;
  device: "cpu" | "gpu" | "gpu-optional";
  params: ParamSpec[];
  changes_timeline: boolean;
  requires_modules: string[];
  requires_models: string[];
  planned: boolean;
  available: boolean;
  unavailable_reason: "planned" | "module_missing" | "model_missing" | null;
}

export interface Preset {
  name: string;
  ops: Record<string, Record<string, unknown>>;
}

export interface OperationsResponse {
  operations: OpSpec[];
  presets: Record<string, Preset>;
}

export interface ResolveResponse {
  ops: { id: string; params: Record<string, unknown> }[];
  disabled: Record<string, string>;
}
