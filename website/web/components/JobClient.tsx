"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import ProgressView from "./ProgressView";
import ResultsView from "./ResultsView";
import { shortId } from "@/lib/format";
import { useJobEvents } from "@/lib/useJobEvents";
import type { Job } from "@/lib/types";

interface JobClientProps {
  id: string;
}

function statusChip(
  phase: "loading" | "running" | "done" | "error",
  job: Job | null,
): { label: string; className: string } {
  switch (phase) {
    case "loading":
      return { label: "loading", className: "status-chip" };
    case "running":
      return job?.status === "queued"
        ? { label: "queued", className: "status-chip is-running" }
        : { label: "running", className: "status-chip is-running" };
    case "done":
      return { label: "done", className: "status-chip is-done" };
    case "error":
      return { label: "error", className: "status-chip is-error" };
  }
}

export default function JobClient({ id }: JobClientProps) {
  const router = useRouter();
  const stream = useJobEvents(id);
  const chip = statusChip(stream.phase, stream.job);

  return (
    <main id="main" className="container">
      <div className="job-head">
        <div className="job-head-left">
          <Link href="/" className="nav-link">
            ← new job
          </Link>
          <h1 className="job-title">Edit job</h1>
          <span className="job-id">{id}</span>
        </div>
        <span className={chip.className}>{chip.label}</span>
      </div>

      {stream.phase === "loading" && (
        <p className="loading-note">loading job {shortId(id)}…</p>
      )}

      {stream.phase === "error" && (
        <section className="panel state-panel" role="alert">
          <h2 className="state-title">Job didn&apos;t finish</h2>
          <p className="state-copy">{stream.error}</p>
          <div className="btn-row">
            <Link
              className="btn btn-primary"
              href="/"
              onClick={() => router.prefetch("/")}
            >
              Try again — start over
            </Link>
            <span className="field-hint">
              check the sidecar logs for the full traceback
            </span>
          </div>
        </section>
      )}

      {stream.phase === "running" && (
        <ProgressView
          stage={stream.stage}
          percent={stream.percent}
          message={stream.message}
          logs={stream.logs}
          notice={stream.notice}
        />
      )}

      {stream.phase === "done" && stream.result && (
        <ResultsView jobId={id} result={stream.result} job={stream.job} />
      )}
    </main>
  );
}
