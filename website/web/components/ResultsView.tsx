"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Toggle from "./Toggle";
import { createJob, getPresets, jobFileUrl, messageOf } from "@/lib/api";
import { readJobDraft, saveJobDraft } from "@/lib/drafts";
import { formatPercent, formatSeconds, shortId } from "@/lib/format";
import type {
  Job,
  JobOptions,
  JobResult,
  Lang,
  Preset,
} from "@/lib/types";

interface ResolvedSettings {
  upload_id: string;
  lang: Lang | null;
  options: JobOptions;
}

interface ResultsViewProps {
  jobId: string;
  result: JobResult;
  job: Job | null;
}

const FILE_KINDS: Array<{ field: keyof JobResult; kind: string }> = [
  { field: "edited", kind: "edited video (mp4)" },
  { field: "srt", kind: "captions (srt)" },
  { field: "nle_zip", kind: "timeline project (zip)" },
  { field: "cutlist", kind: "cut decisions (json)" },
  { field: "ass", kind: "captions (ass)" },
  { field: "words", kind: "word timings (json)" },
];

export default function ResultsView({ jobId, result, job }: ResultsViewProps) {
  const router = useRouter();
  const { stats } = result;

  // Re-render form state. sessionStorage is only readable in the browser, so
  // resolve settings after mount (avoids an SSR hydration mismatch).
  const [settings, setSettings] = useState<ResolvedSettings | null>(null);
  const [settingsResolved, setSettingsResolved] = useState(false);
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [presetsError, setPresetsError] = useState<string | null>(null);
  const [nextStyle, setNextStyle] = useState(stats.style);
  const [burn, setBurn] = useState(true);
  const [busy, setBusy] = useState(false);
  const [rerenderError, setRerenderError] = useState<string | null>(null);

  useEffect(() => {
    if (job?.upload_id && job.options) {
      setSettings({
        upload_id: job.upload_id,
        lang: job.lang ?? null,
        options: job.options,
      });
    } else {
      const draft = readJobDraft(jobId);
      if (draft) {
        setSettings({
          upload_id: draft.upload_id,
          lang: draft.lang,
          options: draft.options,
        });
      }
    }
    setSettingsResolved(true);

    getPresets()
      .then((response) => {
        setPresets(response.presets);
        setPresetsError(null);
      })
      .catch((err: unknown) => setPresetsError(messageOf(err)));
  }, [jobId, job]);

  useEffect(() => {
    if (settings) setBurn(settings.options.burn);
  }, [settings]);

  async function handleRerender(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!settings || busy) return;
    const options: JobOptions = { ...settings.options, burn };
    setBusy(true);
    setRerenderError(null);
    try {
      const newId = await createJob({
        upload_id: settings.upload_id,
        lang: settings.lang,
        style: nextStyle,
        reuse_words_of: jobId,
        options,
      });
      saveJobDraft(newId, {
        upload_id: settings.upload_id,
        lang: settings.lang,
        style: nextStyle,
        options,
      });
      router.push(`/job/${newId}`);
    } catch (err) {
      setRerenderError(messageOf(err));
      setBusy(false);
    }
  }

  const dropped = Object.entries(stats.dropped ?? {}).sort(
    (a, b) => b[1] - a[1],
  );
  const maxDropped = dropped.reduce((max, [, count]) => Math.max(max, count), 0);

  return (
    <>
      <div className="results-grid">
        <div>
          <div className="video-frame">
            <video
              className="result-video"
              controls
              preload="metadata"
              src={jobFileUrl(jobId, result.edited, false)}
              aria-label="Edited video preview"
            >
              Your browser can&apos;t play this file — download{" "}
              {result.edited} below.
            </video>
          </div>
          <p className="video-note">
            preview streams from the sidecar · {stats.brain} brain · style{" "}
            {stats.style}
          </p>

          <div className="section-head">
            <span className="section-num">01</span>
            <h2 className="section-title">Download</h2>
            <span className="section-rule" aria-hidden="true" />
          </div>
          <div className="dl-grid">
            {FILE_KINDS.map(({ field, kind }) => {
              const name = result[field];
              if (typeof name !== "string" || name.length === 0) return null;
              return (
                <a
                  key={field}
                  className="dl"
                  href={jobFileUrl(jobId, name, true)}
                  download
                >
                  <span className="name">{name}</span>
                  <span className="kind">{kind}</span>
                </a>
              );
            })}
          </div>
        </div>

        <aside className="stats-card" aria-label="Edit statistics">
          <p className="stats-title">stats</p>
          <dl className="stat-grid">
            <div className="stat">
              <dt>Kept / source</dt>
              <dd>
                {formatSeconds(stats.kept)}
                <span className="sub">of {formatSeconds(stats.source_duration)}</span>
              </dd>
            </div>
            <div className="stat">
              <dt>Keep ratio</dt>
              <dd>{formatPercent(stats.keep_ratio)}</dd>
            </div>
            <div className="stat">
              <dt>Cut ranges</dt>
              <dd>{stats.ranges}</dd>
            </div>
            <div className="stat">
              <dt>Caption cues</dt>
              <dd>{stats.cues}</dd>
            </div>
            <div className="stat">
              <dt>Brain</dt>
              <dd>{stats.brain}</dd>
            </div>
            <div className="stat">
              <dt>Language</dt>
              <dd>{stats.lang ?? "auto"}</dd>
            </div>
            <div className="stat">
              <dt>Style</dt>
              <dd>{stats.style}</dd>
            </div>
          </dl>

          <div className="hist-block">
            <p className="stats-title">dropped by reason</p>
            {dropped.length === 0 ? (
              <p className="hist-empty">nothing was dropped.</p>
            ) : (
              <div className="hist">
                {dropped.map(([reason, count]) => (
                  <div className="hist-row" key={reason}>
                    <span className="hist-name">{reason}</span>
                    <span className="hist-track">
                      <span
                        className="hist-fill"
                        style={{
                          width: `${maxDropped > 0 ? (count / maxDropped) * 100 : 100}%`,
                        }}
                      />
                    </span>
                    <span className="hist-count">{count}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>
      </div>

      <section className="panel rerender" aria-labelledby="rerender-title">
        <h2 className="rerender-title" id="rerender-title">
          Re-render with a different style
        </h2>
        <p className="rerender-copy">
          Reuses this job&apos;s transcript and cut list — no re-transcription,
          so it finishes much faster than a fresh job. Same video, same
          language, same cut options.
        </p>

        {!settingsResolved ? null : settings ? (
          <form onSubmit={handleRerender}>
            <div className="field-grid" style={{ marginBottom: "1rem" }}>
              <div className="field">
                <label className="field-label" htmlFor="rerender-style">
                  Caption style
                </label>
                <div className="select-wrap">
                  <select
                    id="rerender-style"
                    className="select"
                    value={nextStyle}
                    disabled={presets === null}
                    onChange={(event) => setNextStyle(event.target.value)}
                  >
                    {presets === null ? (
                      <option value="">
                        {presetsError ? "styles unavailable" : "loading styles…"}
                      </option>
                    ) : (
                      presets.map((preset) => (
                        <option key={preset.id} value={preset.id}>
                          {preset.id} · {preset.mode}
                        </option>
                      ))
                    )}
                  </select>
                </div>
                {presetsError && (
                  <p className="field-error field-error-inline" role="alert">
                    couldn&apos;t load styles: {presetsError}
                  </p>
                )}
              </div>
              <div className="field">
                <Toggle
                  id="rerender-burn"
                  label="Burn captions into the video"
                  hint="SRT/ASS are written either way"
                  checked={burn}
                  onChange={setBurn}
                />
              </div>
            </div>
            <div className="btn-row">
              <button
                type="submit"
                className="btn btn-primary"
                disabled={busy || presets === null}
              >
                {busy ? "Re-rendering…" : "Re-render"}
              </button>
              <span className="field-hint">
                reuses words of job {shortId(jobId)}
              </span>
            </div>
            {rerenderError && (
              <p className="field-error" role="alert">
                {rerenderError}
              </p>
            )}
          </form>
        ) : (
          <>
            <p className="field-error" role="alert">
              this session doesn&apos;t hold the original job settings, so
              re-render isn&apos;t available here.
            </p>
            <div className="btn-row">
              <Link className="btn" href="/">
                Start a new job instead
              </Link>
            </div>
          </>
        )}
      </section>
    </>
  );
}
