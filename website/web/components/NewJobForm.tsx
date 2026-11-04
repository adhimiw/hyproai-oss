"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import DropZone from "./DropZone";
import Toggle from "./Toggle";
import {
  createJob,
  getHealth,
  getPresets,
  messageOf,
  uploadVideo,
} from "@/lib/api";
import { saveJobDraft } from "@/lib/drafts";
import { formatBytes, formatClock } from "@/lib/format";
import {
  LANGUAGES,
  type Brain,
  type Health,
  type JobOptions,
  type Lang,
  type Preset,
  type UploadMeta,
} from "@/lib/types";

const MAX_BYTES = 500 * 1024 * 1024; // ≤ 500 MB
const MAX_SECONDS = 600; // ≤ 10 min

/** Read duration from local metadata so we can reject > 10 min up front. */
function probeDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    if (typeof document === "undefined") {
      resolve(null);
      return;
    }
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.preload = "metadata";
    let settled = false;
    const finish = (value: number | null) => {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(url);
      video.src = "";
      resolve(value);
    };
    video.onloadedmetadata = () =>
      finish(Number.isFinite(video.duration) ? video.duration : null);
    video.onerror = () => finish(null);
    window.setTimeout(() => finish(null), 5000);
    video.src = url;
  });
}

export default function NewJobForm() {
  const router = useRouter();

  // upload state
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [meta, setMeta] = useState<UploadMeta | null>(null);
  const pickSeq = useRef(0);

  // server-side settings
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [presetsError, setPresetsError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);

  // control values
  const [lang, setLang] = useState<Lang | "">("");
  const [style, setStyle] = useState("");
  const [silence, setSilence] = useState(true);
  const [retake, setRetake] = useState(false);
  const [fillers, setFillers] = useState(false);
  const [burn, setBurn] = useState(true);
  const [enhance, setEnhance] = useState(true);
  const [brain, setBrain] = useState<Brain | null>(null);

  // start
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  function loadSettings() {
    getPresets()
      .then((response) => {
        setPresets(response.presets);
        setPresetsError(null);
        setStyle((current) => current || response.presets[0]?.id || "");
      })
      .catch((err: unknown) => setPresetsError(messageOf(err)));
    getHealth()
      .then((response) => {
        setHealth(response);
        setHealthError(null);
        setBrain((current) => {
          if (current === "gemini" && !response.gemini_key) return "local";
          if (current) return current;
          return response.gemini_key ? "gemini" : "local";
        });
      })
      .catch((err: unknown) => setHealthError(messageOf(err)));
  }

  useEffect(() => {
    loadSettings();
  }, []);

  async function handlePick(picked: File) {
    pickSeq.current += 1;
    const seq = pickSeq.current;
    setFile(picked);
    setMeta(null);
    setUploadError(null);

    if (!picked.type.startsWith("video/")) {
      setProbing(false);
      setFileError(
        `"${picked.name}" is not a video (type ${picked.type || "unknown"}). Pick a video/* file — mp4, mov, mkv or webm.`,
      );
      return;
    }
    if (picked.size > MAX_BYTES) {
      setProbing(false);
      setFileError(
        `${formatBytes(picked.size)} is over the 500 MB limit. Trim the video or export a smaller file.`,
      );
      return;
    }

    setProbing(true);
    const duration = await probeDuration(picked);
    if (seq !== pickSeq.current) return; // another pick superseded this one
    setProbing(false);
    if (duration !== null && duration > MAX_SECONDS) {
      setFileError(
        `That video runs ${formatClock(duration)} — the limit is 10 minutes. Trim it first.`,
      );
      return;
    }
    setFileError(null);
  }

  async function handleUpload() {
    if (!file || fileError || probing) return;
    setUploading(true);
    setUploadError(null);
    try {
      const uploaded = await uploadVideo(file);
      setMeta(uploaded);
    } catch (err) {
      setMeta(null);
      setUploadError(messageOf(err));
    } finally {
      setUploading(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!meta || !style || !brain || starting) return;

    const options: JobOptions = {
      silence,
      retake,
      fillers,
      burn,
      enhance_audio: enhance,
      brain,
      model: "small",
    };
    const langValue: Lang | null = lang === "" ? null : lang;

    setStarting(true);
    setStartError(null);
    try {
      const id = await createJob({
        upload_id: meta.id,
        lang: langValue,
        style,
        options,
      });
      saveJobDraft(id, { upload_id: meta.id, lang: langValue, style, options });
      router.push(`/job/${id}`);
    } catch (err) {
      setStartError(messageOf(err));
      setStarting(false);
    }
  }

  const uploaded = meta !== null;
  const controlsDisabled = !uploaded;
  const geminiUnavailable = health !== null && !health.gemini_key;
  const settingsBroken = presetsError !== null || healthError !== null;

  let startHint = "queued jobs stream live progress";
  if (!uploaded) startHint = "upload a video first";
  else if (!style) startHint = "pick a caption style";
  else if (brain === null) startHint = healthError ? "server health unknown" : "one moment…";

  return (
    <form onSubmit={handleSubmit}>
      {/* ---------- 01 upload ---------- */}
      <div className="section-head">
        <span className="section-num">01</span>
        <h2 className="section-title">Upload</h2>
        <span className="section-rule" aria-hidden="true" />
      </div>
      <div className="panel">
        <DropZone
          file={file}
          error={fileError}
          disabled={uploading || probing}
          onFile={(picked) => void handlePick(picked)}
        />
        <div className="file-row">
          <button
            type="button"
            className="btn"
            disabled={!file || !!fileError || probing || uploading || uploaded}
            onClick={() => void handleUpload()}
          >
            {uploading
              ? "Uploading…"
              : uploaded
                ? "Uploaded"
                : "Upload video"}
          </button>
          {probing && <span className="field-hint">checking duration…</span>}
        </div>
        {uploadError && (
          <p className="field-error" role="alert">
            {uploadError}
          </p>
        )}
        {meta && (
          <p className="meta-chip">
            uploaded ✓ {meta.width}×{meta.height} · {formatClock(meta.duration)}{" "}
            · {meta.fps} fps · {formatBytes(meta.size)}
          </p>
        )}
        <p className="limits">≤ 10 min · ≤ 500 MB · video files only</p>
      </div>

      {/* ---------- 02 controls ---------- */}
      <div className="section-head">
        <span className="section-num">02</span>
        <h2 className="section-title">Controls</h2>
        <span className="section-rule" aria-hidden="true" />
      </div>
      <div className="panel">
        <fieldset className="options-fieldset" disabled={controlsDisabled}>
          <legend>Video options</legend>
          {!uploaded && (
            <p className="field-hint" style={{ marginBottom: "0.9rem" }}>
              Upload a video to unlock these.
            </p>
          )}

          <div className="field-grid" style={{ marginBottom: "1.1rem" }}>
            <div className="field">
              <label className="field-label" htmlFor="lang">
                Language
              </label>
              <div className="select-wrap">
                <select
                  id="lang"
                  className="select"
                  value={lang}
                  onChange={(event) => setLang(event.target.value as Lang | "")}
                >
                  <option value="">Auto-detect</option>
                  {LANGUAGES.map((entry) => (
                    <option key={entry.code} value={entry.code}>
                      {entry.label}
                    </option>
                  ))}
                </select>
              </div>
              <p className="field-hint">leave on auto-detect if unsure</p>
            </div>

            <div className="field">
              <label className="field-label" htmlFor="style">
                Caption style
              </label>
              <div className="select-wrap">
                <select
                  id="style"
                  className="select"
                  value={style}
                  disabled={presets === null}
                  onChange={(event) => setStyle(event.target.value)}
                >
                  {presets === null ? (
                    <option value="">
                      {presetsError ? "styles unavailable" : "loading styles…"}
                    </option>
                  ) : (
                    presets.map((preset) => (
                      <option key={preset.id} value={preset.id}>
                        {preset.name ?? preset.id} · {preset.mode}
                      </option>
                    ))
                  )}
                </select>
              </div>
              {presetsError ? (
                <>
                  <p className="field-error field-error-inline" role="alert">
                    couldn&apos;t load styles: {presetsError}
                  </p>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={loadSettings}
                  >
                    Retry
                  </button>
                </>
              ) : (
                <p className="field-hint">
                  {presets?.find((preset) => preset.id === style)
                    ? `preset id: ${style}`
                    : presets
                      ? `${presets.length} presets from the sidecar`
                      : "loading styles…"}
                </p>
              )}
            </div>
          </div>

          <div className="toggle-grid">
            <Toggle
              id="opt-silence"
              label="Remove silence"
              hint="cut dead air longer than ~0.6 s"
              checked={silence}
              onChange={setSilence}
            />
            <Toggle
              id="opt-retake"
              label="Remove retakes"
              hint="drop false starts and do-overs"
              checked={retake}
              onChange={setRetake}
            />
            <Toggle
              id="opt-fillers"
              label="Remove fillers"
              hint="drop “um”, “uh”, “enna” and friends"
              checked={fillers}
              onChange={setFillers}
            />
            <Toggle
              id="opt-burn"
              label="Burn captions into the video"
              hint="off keeps the video clean; SRT/ASS are always written"
              checked={burn}
              onChange={setBurn}
            />
            <Toggle
              id="opt-enhance"
              label="Enhance audio"
              hint="denoise and even out loudness"
              checked={enhance}
              onChange={setEnhance}
            />
          </div>

          <fieldset className="brain-group" disabled={health === null || healthError !== null}>
            <legend>Transcription brain</legend>
            <div className="radio-row">
              <label className="radio">
                <input
                  type="radio"
                  name="brain"
                  value="gemini"
                  checked={brain === "gemini"}
                  disabled={health === null || geminiUnavailable}
                  aria-describedby={geminiUnavailable ? "brain-help" : undefined}
                  onChange={() => setBrain("gemini")}
                />
                Gemini
              </label>
              <label className="radio">
                <input
                  type="radio"
                  name="brain"
                  value="local"
                  checked={brain === "local"}
                  disabled={health === null}
                  onChange={() => setBrain("local")}
                />
                Local whisper
              </label>
            </div>
            {health === null && healthError === null && (
              <p className="helper">checking server…</p>
            )}
            {geminiUnavailable && (
              <p className="helper" id="brain-help">
                server has no GEMINI_API_KEY
              </p>
            )}
            {healthError && (
              <>
                <p className="helper helper-error" role="alert">
                  couldn&apos;t reach GET /health: {healthError}
                </p>
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={loadSettings}
                >
                  Retry
                </button>
              </>
            )}
          </fieldset>
        </fieldset>
      </div>

      {/* ---------- 03 start ---------- */}
      <div className="section-head">
        <span className="section-num">03</span>
        <h2 className="section-title">Start</h2>
        <span className="section-rule" aria-hidden="true" />
      </div>
      <div className="panel">
        <div className="btn-row">
          <button
            type="submit"
            className="btn btn-primary btn-lg"
            disabled={!uploaded || !style || brain === null || starting}
          >
            {starting ? "Starting…" : "Start editing"}
          </button>
          <span className="field-hint">{startHint}</span>
        </div>
        {startError && (
          <p className="field-error" role="alert">
            {startError}
          </p>
        )}
        {settingsBroken && !startError && (
          <p className="field-error" role="alert">
            server settings didn&apos;t fully load — retry the sections above
            before starting.
          </p>
        )}
      </div>
    </form>
  );
}
