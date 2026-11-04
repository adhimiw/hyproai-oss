# website/API_CONTRACT.md — the interface between sidecar, web UI, and ops

Single source of truth for the website stage. Sidecar (Python/FastAPI) and web
(Next.js) are built independently against this file; any mismatch is a bug in
the implementation, not in this doc.

## Layout

```
website/
  API_CONTRACT.md      ← this file
  sidecar/             ← Python FastAPI service (wraps hypro_core)
    app.py             ← routes
    jobs.py            ← job store + SSE event log
    pipeline.py        ← orchestrator (core stages + progress)
    brain.py           ← gemini-3.5-transcribe adapter + local fallback
    test_sidecar.py    ← acceptance tests (run with repo .venv python)
    requirements.txt
  web/                 ← Next.js (App Router, TypeScript)
  docker-compose.yml   ← web + sidecar + shared volume   (ops owns)
  Dockerfile.web / Dockerfile.sidecar                     (ops owns)
```

- The sidecar imports the core by inserting `<repo>/core` into `sys.path`
  itself (works locally and in Docker without env vars).
- Data root: `HYPRO_DATA_DIR` env, default `<repo>/data` (git-ignored).
  Layout: `data/uploads/<upload_id>/input.<ext>`, `data/jobs/<job_id>/files/*`.
- The sidecar enables CORS (`allow_origins=["*"]`) so the UI may bypass the
  Next proxy if SSE-through-rewrite ever misbehaves.

## Sidecar HTTP API (default `http://localhost:8787`)

All JSON uses these exact field names. Errors: `{"error": "<human message>"}`
with 4xx/5xx.

### `GET /health` → 200
```json
{ "ok": true, "gemini_key": true, "version": "0.1.0" }
```

### `GET /presets` → 200
```json
{ "presets": [ { "id": "tiktok-karaoke", "mode": "karaoke_sweep", "font": "Montserrat", "size": 76, "...": "full preset JSON" } ] }
```
Source of truth: `core/hypro_core/caption_presets/*.json`, `id` = filename stem.

### `POST /uploads` — `multipart/form-data`, field `file`
Limits: ≤ 500 MB (else **413**), ≤ 600 s duration (else **413**), must have a
video stream (else **400**). Stores `input.<ext>` under `uploads/<uuid>/`.
→ **201**
```json
{ "id": "uuid", "name": "reel.mp4", "duration": 30.01,
  "width": 1080, "height": 1920, "fps": 30.0,
  "has_audio": true, "size": 12345678 }
```

### `POST /jobs` → **202** `{ "id": "uuid" }`
```json
{
  "upload_id": "uuid",
  "lang": "ta",                 // null/absent = auto-detect; else one of hi ta te ml kn bn
  "style": "tiktok-karaoke",    // must exist in /presets else 400
  "reuse_words_of": null,       // optional job id → skip transcribe, reuse its words.json
  "options": {
    "silence": true, "retake": false, "fillers": false,
    "burn": true, "enhance_audio": true,
    "brain": "gemini",          // "gemini" | "local"
    "model": "small"            // local whisper model (fallback / brain=local)
  }
}
```
404 unknown upload · 400 bad style/lang/upload. Jobs run in a background
thread; one process, in-memory job dict (v1 — Redis later).

### `GET /jobs/{id}` → 200
```json
{
  "id": "uuid", "status": "queued|running|done|error",
  "stage": "probe|transcribe|analyze|captions|audio|render|export|done",
  "percent": 42, "message": "transcribing with gemini",
  "error": null,
  "result": null
}
```
When `status == "done"`, `result` is:
```json
{
  "edited": "edited.mp4", "srt": "captions.srt", "ass": "captions.ass",
  "nle_zip": "nle.zip", "cutlist": "cutlist.json", "words": "words.json",
  "stats": {
    "source_duration": 30.01, "kept": 28.4, "keep_ratio": 0.946,
    "ranges": 2, "cues": 10, "dropped": { "silence": 2 },
    "brain": "gemini", "lang": "ta", "style": "tiktok-karaoke"
  }
}
```
File fields are names — fetch them from `GET /jobs/{id}/files/{name}`.

`stats.ranges` is the number of **keep** ranges in the cutlist (contiguous
segments that survive), not the number of cuts — N silences in the middle
leave N+1 keep ranges. `stats.dropped` is a reason → count histogram.

### `GET /jobs/{id}/events` — SSE
- Replays the full stored event log first (refresh-safe), then follows live.
- Heartbeat comment `: ping` every ~15 s.
- Events (SSE `event:` name, JSON in `data:`):
  - `progress` → `{ "stage": "...", "percent": 42, "message": "..." }`
  - `log` → `{ "stage": "...", "message": "..." }` (non-fatal notices, e.g.
    `"gemini unavailable (429) — falling back to local whisper"`)
  - `done` → `{ "result": { ...same as GET result... } }` then **close**
  - `error` → `{ "error": "..." }` then **close**
- Percent mapping (shared so the UI is consistent):
  probe 0→2 · transcribe 2→45 · analyze 45→52 · captions 52→60 ·
  audio 60→72 · render 72→92 · export 92→100.
  A `reuse_words_of` job still walks the same stages (transcribe completes
  instantly with message `"reusing cached transcript"`).

### `GET /jobs/{id}/files/{name}` → bytes
Allowed: `edited.mp4`, `captions.srt`, `captions.ass`, `cutlist.json`,
`words.json`, `nle.zip`. `?dl=1` adds `Content-Disposition: attachment`.
200 with correct `Content-Type` (`video/mp4`, `application/zip`,
`application/json`, `text/plain; charset=utf-8` for srt/ass) · 404 otherwise.

## Gemini brain (`brain.py`)

Primary path when `options.brain == "gemini"` (and a key exists — server env
`GEMINI_API_KEY`, or per-request `gemini_key` override is NOT passed through
jobs in v1; env only). **Any failure falls back to local faster-whisper and
emits a `log` event — never fails the job for brain reasons.**

Proven reference code (both checked in):
- `scripts/probe_transcribe_tamil.py` — Files API resumable upload (needs
  `X-Goog-Upload-Offset: 0`), interactions call, Tamil-verified.
- `scripts/gemini_export_experiment.py` — retry/backoff honoring
  `"retry in Ns"` hints.

Flow: ffmpeg-extract `audio.mp3` (16 kHz mono) → Files API upload →
`POST /v1beta/interactions` with
```json
{ "model": "models/gemini-3.5-transcribe",
  "input": [{ "type": "audio", "uri": "...", "mime_type": "audio/mp3" }],
  "generation_config": { "transcription_config": {
      "mode": { "type": "verbatim", "timestamp_granularities": ["word"] },
      "language_codes": [] } },
  "store": false }
```
(`language_codes` = `[]` for auto, else BCP-47 map:
hi→hi-IN ta→ta-IN te→te-IN ml→ml-IN kn→kn-IN bn→bn-IN; `ta-IN` is unlisted in
the docs table but accepted — research/12.)
Parse: `steps[]` where `type=="model_output"` → `content[]` where
`type=="text"` → `text` + `annotations[]` where `type=="word_info"` →
`{text, start_offset, end_offset}` with values like `"0.100s"`
(`float(v.rstrip("s"))`). Build `hypro_core.timing.Word(t, s, e, conf=1.0)`
and a `Transcript`; language = hint if given else
`hypro_core.romanize.detect_lang(full_text)`.

## Pipeline (`pipeline.py`) — mirrors `hypro_core.cli` `cmd_edit`

probe → transcribe(brain) → build_cutlist → group_cues + finalize_cues
(romanize, `remap_to_record`) → compile_ass → enhance_ffmpeg (optional) →
render_edited (burn after concat) → export_nle → zip `nle/` → stats.
Artifacts land in `data/jobs/<id>/files/`. Every stage emits `progress`;
unexpected exceptions → `status=error` + `error` event (message must name the
stage). `words.json` is written on every job (needed for rerender).

## Web UI (Next.js)

- Browser talks **only** to `/api/sidecar/*` (Next rewrite →
  `process.env.SIDECAR_URL || http://localhost:8787`). Escape hatch:
  `NEXT_PUBLIC_SIDECAR_ORIGIN` set → client calls that origin directly.
- Pages:
  - `/` — hero + drag/drop upload (≤10 min / ≤500 MB stated), language select
    (Auto + hi ta te ml kn bn), style picker (from `/presets`, shows mode),
    option toggles (silence / retake / fillers / burn / enhance audio),
    brain toggle (gemini/local — disabled+explained when `health.gemini_key`
    is false), Start.
  - `/job/[id]` — SSE progress (stage list + percent + message, `aria-live`),
    then results: `<video>` player of `edited.mp4`, stats card (kept/source,
    keep ratio, cues, dropped histogram), download buttons (mp4, srt,
    nle.zip, cutlist.json, captions.ass, words.json), style re-select +
    **Re-render** (POST `/jobs` with `reuse_words_of: <id>`), error state.
- Accessibility: labeled controls, keyboard-reachable drag/drop (file input
  fallback), aria-live on progress, visible focus. Responsive ≥360 px.
- `npm run build` must pass (TypeScript strict).

## Ops (owned by the integrator, not the two agents)

`docker-compose.yml`: `web` (3000, `SIDECAR_URL=http://sidecar:8787`,
`GEMINI_API_KEY` passthrough not needed — key goes to sidecar) + `sidecar`
(8787, `GEMINI_API_KEY`, `HYPRO_DATA_DIR=/data`, volume `hypro-data:/data`,
ffmpeg present). Root README gets a Website section.

## Test gates

- Sidecar: `cd website/sidecar && ../../.venv/bin/python test_sidecar.py`
  — must pass **without a Gemini key** (`brain=local`, `model=tiny` for
  speed): upload → job → SSE → files → stats; plus 413/404/400 paths and a
  unit test for annotations→Word parsing (fixture JSON, no network).
- Web: `cd website/web && npm run build` passes.
- Integration (integrator): full stack, real upload via browser/curl,
  `brain=gemini` once quota allows.
