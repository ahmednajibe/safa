/** TS mirrors of the backend /api payloads (SPEC §5, §9, §11, §12). */

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
  unavailable_code: "planned" | "module_missing" | "model_missing" | null;
}

export interface Preset {
  name: string;
  ops: Record<string, Record<string, unknown>>;
}

export interface OperationsResponse {
  operations: OpSpec[];
  presets: Record<string, Preset>;
}

export interface DisabledOp {
  code: string;
  message: string;
  blocker_op_id: string | null;
}

export interface ResolveResponse {
  ops: { id: string; params: Record<string, unknown> }[];
  disabled: Record<string, DisabledOp>;
}

/* ---------- media ---------- */

export interface AudioStreamInfo {
  index: number;
  codec: string;
  sample_rate: number;
  channels: number;
  channel_layout: string | null;
  language: string | null;
  title: string | null;
  duration_s: number | null;
}

export interface VideoStreamInfo {
  index: number;
  codec: string;
  width: number;
  height: number;
  fps: number | null;
  duration_s: number | null;
}

export interface Probe {
  duration_s: number;
  format_name: string;
  size_bytes: number;
  audio: AudioStreamInfo[];
  video: VideoStreamInfo[];
  has_video: boolean;
}

export interface MediaReady {
  peaks: boolean;
  playback: boolean;
  error: string | null;
}

export interface MediaInfo {
  media_id: string;
  filename: string;
  size_bytes: number;
  probe: Probe;
  lufs: number | null;
  ready?: MediaReady;
}

/* ---------- jobs ---------- */

export interface JobFile {
  name: string;
  kind: "audio" | "video" | "subtitle" | "stem" | string;
  size_bytes: number;
  saved_path: string | null;
}

export interface JobWarning {
  warning_code: string;
  warning_params: Record<string, unknown>;
}

export interface JobStage {
  id: string;
  name: string;
  device: "gpu" | "cpu" | string;
  fallback_reason: string | null;
  state: string;
  actual_device?: string;
  message_code?: string | null;
  message_params?: Record<string, unknown>;
  warning_code?: string | null;
  warning_params?: Record<string, unknown>;
  seconds?: number;
}

export type JobState_ =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface Job {
  id: string;
  media_id: string;
  kind: "full" | "preview" | string;
  ops: { id: string; params: Record<string, unknown> }[];
  output: { audio_format: string; keep_video: boolean };
  preview: { start_s: number; end_s: number } | null;
  requested_device: "gpu" | "cpu";
  audio_stream: number;
  state: JobState_ | string;
  stage: string | null;
  stage_id: string | null;
  stage_idx: number | null;
  total_stages: number | null;
  stage_pct: number | null;
  overall_pct: number | null;
  eta_sec: number | null;
  device: string | null;
  message: string | null;
  error: string | null;
  message_code: string | null;
  message_params: Record<string, unknown>;
  error_code: string | null;
  error_params: Record<string, unknown>;
  warnings: JobWarning[];
  stages: JobStage[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  retry_of: string | null;
  queue_position: number | null;
  files?: JobFile[];
  filename?: string | null;
}

export interface JobsResponse {
  jobs: Job[];
}

/* ---------- estimate / settings / timeline ---------- */

export type EstimateBucket = "under_1m" | "about_n_m" | "over_30m";

export interface Estimate {
  low_s: number;
  high_s: number;
  bucket: string;
  bucket_code: EstimateBucket;
  minutes: number | null;
}

export interface AppSettings {
  output_dir: string | null;
}

export interface TimelineSegment {
  src_start: number;
  src_end: number;
  out_start: number;
  out_end: number;
  speed: number;
}

export interface TimelineData {
  source_duration: number;
  segments: TimelineSegment[];
  removed: [number, number][];
}

/** Error envelope the API returns on non-2xx (code + params + English). */
export class ApiError extends Error {
  code: string;
  params: Record<string, unknown>;

  constructor(code: string, message: string, params: Record<string, unknown> = {}) {
    super(message);
    this.code = code;
    this.params = params;
  }
}
