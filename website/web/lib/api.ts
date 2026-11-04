import type {
  Health,
  Job,
  JobCreateRequest,
  PresetsResponse,
  UploadMeta,
} from "./types";

const DIRECT_ORIGIN = process.env.NEXT_PUBLIC_SIDECAR_ORIGIN;

/**
 * Base the browser talks to: the Next rewrite (`/api/sidecar/...`) by default,
 * or the sidecar origin directly when NEXT_PUBLIC_SIDECAR_ORIGIN is set
 * (SSE escape hatch — the sidecar sends permissive CORS).
 */
export const SIDECAR_BASE: string = DIRECT_ORIGIN
  ? DIRECT_ORIGIN.replace(/\/+$/, "")
  : "/api/sidecar";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Fallback text when the sidecar didn't send its {"error": "..."} body. */
const STATUS_HINTS: Record<number, string> = {
  400: "the sidecar rejected this request (400)",
  404: "not found on the sidecar (404)",
  413: "rejected as too large (413) — the sidecar caps uploads at 500 MB and 10 minutes",
  500: "the sidecar hit an internal error (500)",
  502: "can't reach the sidecar (502)",
};

async function readErrorMessage(res: Response): Promise<string> {
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // non-JSON error body — fall through
  }
  if (body !== null && typeof body === "object" && "error" in body) {
    const raw = (body as { error: unknown }).error;
    if (typeof raw === "string" && raw.trim().length > 0) return raw;
  }
  return STATUS_HINTS[res.status] ?? `request failed (HTTP ${res.status})`;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${SIDECAR_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new ApiError(await readErrorMessage(res), res.status);
  return (await res.json()) as T;
}

async function expectOk(res: Response): Promise<void> {
  if (!res.ok) throw new ApiError(await readErrorMessage(res), res.status);
}

export function getHealth(): Promise<Health> {
  return getJson<Health>("/health");
}

export function getPresets(): Promise<PresetsResponse> {
  return getJson<PresetsResponse>("/presets");
}

export function getJob(id: string): Promise<Job> {
  return getJson<Job>(`/jobs/${encodeURIComponent(id)}`);
}

export async function uploadVideo(file: File): Promise<UploadMeta> {
  const form = new FormData();
  form.append("file", file, file.name);
  const res = await fetch(`${SIDECAR_BASE}/uploads`, {
    method: "POST",
    body: form,
  });
  await expectOk(res);
  return (await res.json()) as UploadMeta;
}

export async function createJob(payload: JobCreateRequest): Promise<string> {
  const res = await fetch(`${SIDECAR_BASE}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  await expectOk(res);
  const body = (await res.json()) as { id: string };
  return body.id;
}

/** SSE endpoint for job progress (see lib/useJobEvents.ts). */
export function jobEventsUrl(id: string): string {
  return `${SIDECAR_BASE}/jobs/${encodeURIComponent(id)}/events`;
}

/** URL for one job artifact; `download` adds `?dl=1` (attachment). */
export function jobFileUrl(id: string, name: string, download: boolean): string {
  const suffix = download ? "?dl=1" : "";
  return `${SIDECAR_BASE}/jobs/${encodeURIComponent(id)}/files/${encodeURIComponent(name)}${suffix}`;
}

/** Human-readable message from any thrown error of this module. */
export function messageOf(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "something went wrong talking to the sidecar";
}
