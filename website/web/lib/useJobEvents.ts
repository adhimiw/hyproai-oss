"use client";

import { useEffect, useState } from "react";
import { getJob, jobEventsUrl, messageOf } from "./api";
import type {
  DonePayload,
  ErrorPayload,
  Job,
  JobLogLine,
  JobResult,
  LogPayload,
  ProgressPayload,
  Stage,
} from "./types";

export type StreamPhase = "loading" | "running" | "done" | "error";

export interface JobStream {
  phase: StreamPhase;
  /** Last known snapshot from GET /jobs/{id} (null until the first load). */
  job: Job | null;
  stage: Stage | null;
  percent: number;
  message: string;
  logs: JobLogLine[];
  result: JobResult | null;
  error: string | null;
  /** Non-fatal notice, e.g. "lost contact with the server — retrying…". */
  notice: string | null;
}

const MAX_LOG_LINES = 300;
const REFETCH_DEBOUNCE_MS = 1500;
const RETRY_BACKOFF_MS = 2000;
const MAX_RETRY_BACKOFF_MS = 10000;

const INITIAL: JobStream = {
  phase: "loading",
  job: null,
  stage: null,
  percent: 0,
  message: "",
  logs: [],
  result: null,
  error: null,
  notice: null,
};

function parse<T>(ev: Event): T | null {
  const data = (ev as MessageEvent<unknown>).data;
  if (typeof data !== "string") return null;
  try {
    return JSON.parse(data) as T;
  } catch {
    return null;
  }
}

/**
 * Loads a job once (`GET /jobs/{id}`), then follows `GET /jobs/{id}/events`
 * over SSE. Reconnect-safe:
 *
 *  - The sidecar replays its full event log on every (re)connect, so a fresh
 *    EventSource never misses state — page refresh just works.
 *  - Connection-level SSE errors are debounced into a re-`GET`: if the job is
 *    done/error there we settle immediately; if it's still running we open a
 *    fresh stream (log replay catches us up). GET failures retry with
 *    capped backoff and surface a `notice`.
 *  - Server-sent `event: error` (a MessageEvent carrying data on the "error"
 *    channel) is distinguished from connection errors by the presence of
 *    `data`, and settles the stream for good.
 *  - The stream settles on `done` / `error`: EventSource is closed and no
 *    further requests are made.
 */
export function useJobEvents(jobId: string): JobStream {
  const [state, setState] = useState<JobStream>(INITIAL);

  useEffect(() => {
    let disposed = false;
    let settled = false;
    let source: EventSource | null = null;
    let refetchTimer: ReturnType<typeof setTimeout> | null = null;
    let retryDelay = RETRY_BACKOFF_MS;

    const clearRefetch = () => {
      if (refetchTimer !== null) {
        clearTimeout(refetchTimer);
        refetchTimer = null;
      }
    };

    const closeStream = () => {
      if (source !== null) {
        source.close();
        source = null;
      }
    };

    const settleDone = (job: Job | null, result: JobResult) => {
      if (settled || disposed) return;
      settled = true;
      clearRefetch();
      closeStream();
      setState((s) => ({
        ...s,
        phase: "done",
        job: job ?? s.job,
        stage: "done",
        percent: 100,
        result,
        notice: null,
      }));
    };

    const settleError = (job: Job | null, message: string) => {
      if (settled || disposed) return;
      settled = true;
      clearRefetch();
      closeStream();
      setState((s) => ({
        ...s,
        phase: "error",
        job: job ?? s.job,
        error: message,
        notice: null,
      }));
    };

    const onProgress = (payload: ProgressPayload | null) => {
      if (!payload || settled || disposed) return;
      clearRefetch();
      setState((s) => ({
        ...s,
        phase: s.phase === "loading" ? "running" : s.phase,
        stage: payload.stage,
        percent: payload.percent,
        message: payload.message,
      }));
    };

    const onLog = (payload: LogPayload | null) => {
      if (!payload || settled || disposed) return;
      clearRefetch();
      setState((s) => {
        const logs = [...s.logs, { stage: payload.stage, message: payload.message }];
        return {
          ...s,
          logs:
            logs.length > MAX_LOG_LINES
              ? logs.slice(logs.length - MAX_LOG_LINES)
              : logs,
        };
      });
    };

    const openStream = () => {
      if (disposed || settled) return;
      closeStream();
      const es = new EventSource(jobEventsUrl(jobId));
      source = es;

      es.addEventListener("progress", (ev) => onProgress(parse<ProgressPayload>(ev)));
      es.addEventListener("log", (ev) => onLog(parse<LogPayload>(ev)));

      es.addEventListener("done", (ev) => {
        const payload = parse<DonePayload>(ev);
        if (payload && payload.result) settleDone(null, payload.result);
      });

      es.addEventListener("error", (ev) => {
        if (disposed || settled) return;
        const payload = parse<ErrorPayload>(ev);
        if (payload !== null && typeof payload.error === "string") {
          // Named server event `event: error` (carries data) → job failed.
          settleError(null, payload.error || "the job failed");
          return;
        }
        // Connection-level error. EventSource may auto-reconnect on its own;
        // verify reality with a GET either way (debounced).
        scheduleRefetch();
      });
    };

    const refetch = () => {
      refetchTimer = null;
      if (disposed || settled) return;
      getJob(jobId)
        .then((job) => {
          if (disposed || settled) return;
          retryDelay = RETRY_BACKOFF_MS;
          if (job.status === "done" && job.result) {
            settleDone(job, job.result);
            return;
          }
          if (job.status === "error") {
            settleError(job, job.error ?? "the job failed");
            return;
          }
          // Still queued/running → resume the stream; the sidecar replays
          // its event log first, so nothing is missed.
          setState((s) => ({
            ...s,
            phase: "running",
            job,
            stage: job.stage,
            percent: job.percent,
            message: job.message,
            notice: null,
          }));
          openStream();
        })
        .catch((err: unknown) => {
          if (disposed || settled) return;
          setState((s) => ({
            ...s,
            notice: `lost contact with the server — retrying (${messageOf(err)})`,
          }));
          retryDelay = Math.min(retryDelay * 2, MAX_RETRY_BACKOFF_MS);
          refetchTimer = setTimeout(refetch, retryDelay);
        });
    };

    const scheduleRefetch = () => {
      if (disposed || settled || refetchTimer !== null) return;
      refetchTimer = setTimeout(refetch, REFETCH_DEBOUNCE_MS);
    };

    // First load: snapshot, then stream.
    getJob(jobId)
      .then((job) => {
        if (disposed) return;
        if (job.status === "done" && job.result) {
          settleDone(job, job.result);
          return;
        }
        if (job.status === "error") {
          settleError(job, job.error ?? "the job failed");
          return;
        }
        setState((s) => ({
          ...s,
          phase: "running",
          job,
          stage: job.stage,
          percent: job.percent,
          message: job.message,
        }));
        openStream();
      })
      .catch((err: unknown) => {
        if (disposed) return;
        // No snapshot to fall back on → show the error state (with retry
        // link home) rather than sitting in "loading" forever.
        settleError(null, messageOf(err));
      });

    return () => {
      disposed = true;
      clearRefetch();
      closeStream();
    };
  }, [jobId]);

  return state;
}
