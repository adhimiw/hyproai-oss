/**
 * Per-tab memory of what was submitted for a job.
 *
 * The contract's `GET /jobs/{id}` doesn't return `upload_id` / full
 * `options`, but the Re-render button on /job/[id] must POST them again with
 * `reuse_words_of`. So the create page saves the request here, keyed by job
 * id. If the sidecar ever returns those fields on the job itself, the UI
 * prefers them (see components/ResultsView.tsx) and this is just a fallback.
 */
import type { JobOptions, Lang } from "./types";

export interface JobDraft {
  upload_id: string;
  lang: Lang | null;
  style: string;
  options: JobOptions;
}

const STORAGE_KEY = "hyproai-oss.job-drafts";
const MAX_DRAFTS = 20;

function readAll(): Record<string, JobDraft> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed !== null && typeof parsed === "object") {
      return parsed as Record<string, JobDraft>;
    }
  } catch {
    // storage unavailable (private mode, quota) — drafts are optional
  }
  return {};
}

export function saveJobDraft(jobId: string, draft: JobDraft): void {
  if (typeof window === "undefined") return;
  try {
    const all = readAll();
    all[jobId] = draft;
    const keys = Object.keys(all);
    for (const key of keys.slice(0, Math.max(0, keys.length - MAX_DRAFTS))) {
      delete all[key];
    }
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(all));
  } catch {
    // best effort only
  }
}

export function readJobDraft(jobId: string): JobDraft | null {
  return readAll()[jobId] ?? null;
}
