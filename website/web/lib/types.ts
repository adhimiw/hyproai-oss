/**
 * Types for the sidecar HTTP API — mirrors website/API_CONTRACT.md exactly.
 * Field names here are the contract; any mismatch is a bug.
 */

export type Lang = "hi" | "ta" | "te" | "ml" | "kn" | "bn";

export const LANGUAGES: ReadonlyArray<{ code: Lang; label: string }> = [
  { code: "hi", label: "Hindi" },
  { code: "ta", label: "Tamil" },
  { code: "te", label: "Telugu" },
  { code: "ml", label: "Malayalam" },
  { code: "kn", label: "Kannada" },
  { code: "bn", label: "Bengali" },
];

/** GET /health */
export interface Health {
  ok: boolean;
  gemini_key: boolean;
  version: string;
}

/** One entry of GET /presets (extra fields pass through untouched). */
export interface Preset {
  id: string;
  mode: string;
  font?: string;
  size?: number;
  [key: string]: unknown;
}

export interface PresetsResponse {
  presets: Preset[];
}

/** POST /uploads → 201 */
export interface UploadMeta {
  id: string;
  name: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  has_audio: boolean;
  size: number;
}

export type Brain = "gemini" | "local";

/** options object of POST /jobs */
export interface JobOptions {
  silence: boolean;
  retake: boolean;
  fillers: boolean;
  burn: boolean;
  enhance_audio: boolean;
  brain: Brain;
  model: string;
}

/** POST /jobs body → 202 */
export interface JobCreateRequest {
  upload_id: string;
  lang?: Lang | null;
  style: string;
  reuse_words_of?: string | null;
  options: JobOptions;
}

export type JobStatus = "queued" | "running" | "done" | "error";

export type Stage =
  | "probe"
  | "transcribe"
  | "analyze"
  | "captions"
  | "audio"
  | "render"
  | "export"
  | "done";

/** Pipeline order, for the stage list on /job/[id]. */
export const STAGES: ReadonlyArray<Exclude<Stage, "done">> = [
  "probe",
  "transcribe",
  "analyze",
  "captions",
  "audio",
  "render",
  "export",
];

export interface JobStats {
  source_duration: number;
  kept: number;
  keep_ratio: number;
  ranges: number;
  cues: number;
  dropped: Record<string, number>;
  brain: string;
  lang: string | null;
  style: string;
}

/** `result` of a done job (GET /jobs/{id} and the `done` SSE event). */
export interface JobResult {
  edited: string;
  srt: string;
  ass: string;
  nle_zip: string;
  cutlist: string;
  words: string;
  stats: JobStats;
}

/** GET /jobs/{id} */
export interface Job {
  id: string;
  status: JobStatus;
  stage: Stage;
  percent: number;
  message: string;
  error: string | null;
  result: JobResult | null;
  /**
   * Not in the contract's example payload, but harmless if the sidecar adds
   * them: the UI prefers these (over the local sessionStorage draft) when
   * rebuilding a re-render request. See lib/drafts.ts.
   */
  upload_id?: string;
  lang?: Lang | null;
  style?: string;
  options?: JobOptions;
}

/** SSE `event: progress` payload */
export interface ProgressPayload {
  stage: Stage;
  percent: number;
  message: string;
}

/** SSE `event: log` payload */
export interface LogPayload {
  stage: Stage;
  message: string;
}

/** SSE `event: done` payload */
export interface DonePayload {
  result: JobResult;
}

/** SSE `event: error` payload */
export interface ErrorPayload {
  error: string;
}

export interface JobLogLine {
  stage: Stage;
  message: string;
}
