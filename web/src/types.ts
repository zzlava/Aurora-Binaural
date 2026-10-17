export type ControlKey = "strength" | "width" | "height" | "room" | "voice_distance";
export type Controls = Partial<Record<ControlKey, number>>;

export interface Preset {
  id: string;
  name: string;
  version: string;
  default_strength: number;
  default_controls: Controls;
  description: string;
  route: string;
}
export interface UserPreset {
  user_preset_id: string;
  name: string;
  preset: { id: string; version: string };
  controls: Controls;
}
export interface EngineStatus {
  is_stub: boolean;
  available: boolean;
  message: string;
  version?: string;
  hrtf?: { name: string };
  supported_routes?: string[];
}
export interface Asset {
  asset_id: string;
  filename: string;
  duration_seconds: number;
  sample_rate: number;
  samples: number;
  channels: number;
}
export interface PreviewSpec {
  start_seconds: number;
  duration_seconds: number;
}
export interface Job {
  job_id: string;
  asset_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "canceled";
  canceling: boolean;
  progress: number;
  attempts: number;
  preview: PreviewSpec | null;
  error: { code: string; message: string; details?: unknown } | null;
  result_metadata: {
    sample_rate: number;
    samples: number;
    format: string;
    duration_seconds: number;
  } | null;
}
export interface Loudness {
  integrated_lufs: number | null;
  true_peak_dbtp: number | null;
}
export interface Report {
  controls: Controls;
  effective_params: Record<string, unknown> | null;
  ignored_controls: string[];
  controls_note: string;
  engine: {
    is_stub: boolean;
    name: string;
    version: string;
    route: string | null;
    hrtf: { name: string };
  };
  output: { sample_rate: number; samples: number };
  loudness_before: Loudness;
  loudness_after: Loudness;
  params: {
    preset: { id: string; version: string };
    strength: number;
    controls: Controls;
    seed: number;
  };
  preview: null | {
    original_loudness: Loudness;
    loudness_diff_lu: number | null;
    match_status: string;
    actual_duration_seconds: number;
    start_seconds: number;
  };
}
export interface QueueEntry {
  job: Job;
  filename: string;
  report?: Report;
  issue?: string;
}
