"use client";

import { useEffect, useRef } from "react";
import { STAGES, type JobLogLine, type Stage } from "@/lib/types";

type StageKey = Exclude<Stage, "done">;

const STAGE_LABELS: Record<StageKey, string> = {
  probe: "Probe media",
  transcribe: "Transcribe",
  analyze: "Analyze cuts",
  captions: "Build captions",
  audio: "Enhance audio",
  render: "Render edit",
  export: "Export NLE",
};

/** Upper bound of each stage's percent window (API_CONTRACT.md). */
const STAGE_BOUNDS: Record<StageKey, number> = {
  probe: 2,
  transcribe: 45,
  analyze: 52,
  captions: 60,
  audio: 72,
  render: 92,
  export: 100,
};

interface ProgressViewProps {
  stage: Stage | null;
  percent: number;
  message: string;
  logs: JobLogLine[];
  notice: string | null;
}

function stageState(
  index: number,
  current: Stage | null,
  percent: number,
): "done" | "active" | "pending" {
  if (current === "done" || percent >= 100) return "done";
  if (!current) return "pending";
  const currentIndex = STAGES.indexOf(current as StageKey);
  if (currentIndex < 0) {
    // Unknown stage — fall back to the shared percent windows.
    const bound = STAGE_BOUNDS[STAGES[index] as StageKey] ?? 100;
    const floor = index === 0 ? 0 : (STAGE_BOUNDS[STAGES[index - 1] as StageKey] ?? 0);
    if (percent > floor && percent <= bound) return "active";
    return percent > bound ? "done" : "pending";
  }
  if (index < currentIndex) return "done";
  if (index === currentIndex) return "active";
  return "pending";
}

function stageMark(state: "done" | "active" | "pending"): string {
  if (state === "done") return "✓";
  if (state === "active") return "▸";
  return "·";
}

function stageStatusText(state: "done" | "active" | "pending"): string {
  if (state === "done") return "done";
  if (state === "active") return "working…";
  return "pending";
}

export default function ProgressView({
  stage,
  percent,
  message,
  logs,
  notice,
}: ProgressViewProps) {
  const consoleRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = consoleRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logs]);

  const currentLabel = stage && stage !== "done" ? STAGE_LABELS[stage as StageKey] : stage === "done" ? "Finished" : "Starting";

  return (
    <div className="progress-grid">
      {/* Progress region — politely announced as it updates. */}
      <section className="panel" aria-label="Job progress" aria-live="polite">
        <div className="progress-live">
          <div>
            <p className="now-stage">{currentLabel}</p>
            <p className="pct">{percent}%</p>
          </div>
          <div className="progress-detail">
            <p className="now-msg">{message || "waiting for the sidecar…"}</p>
            <div
              className="bar"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
              aria-label="Overall job progress"
            >
              <span
                className="bar-fill"
                style={{ width: `${Math.max(0, Math.min(100, percent))}%` }}
              />
            </div>
          </div>
        </div>

        <ol className="stage-list" style={{ marginTop: "1.1rem" }}>
          {STAGES.map((stageKey, index) => {
            const state = stageState(index, stage, percent);
            return (
              <li
                key={stageKey}
                className="stage-item"
                data-state={state}
                aria-current={state === "active" ? "step" : undefined}
              >
                <span className="idx" aria-hidden="true">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="mark" aria-hidden="true">
                  {stageMark(state)}
                </span>
                <span className="name">{STAGE_LABELS[stageKey]}</span>
                <span
                  className={state === "pending" ? "sr-only" : "when"}
                >
                  {stageStatusText(state)}
                </span>
              </li>
            );
          })}
        </ol>

        {notice && <p className="notice">{notice}</p>}
      </section>

      {/* Log console — deliberately outside the live region. */}
      <section className="panel console-wrap" aria-label="Pipeline log">
        <div className="console-head">
          <p className="console-title">log</p>
          <span className="console-count">{logs.length} lines</span>
        </div>
        <div className="console" ref={consoleRef}>
          {logs.length === 0 ? (
            <p className="console-empty">waiting for the first log line…</p>
          ) : (
            logs.map((line, index) => (
              <p className="console-line" key={`${index}-${line.stage}`}>
                <span className="console-stage">{line.stage}</span>
                <span className="console-msg">{line.message}</span>
              </p>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
