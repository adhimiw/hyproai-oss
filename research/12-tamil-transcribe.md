# 12 — Tamil on `gemini-3.5-transcribe` (the website brain gap)

**Date:** 2026-11-04 · **Method:** `scripts/probe_transcribe_tamil.py` (checked in, re-runnable)
**Question:** the supported-languages table in the transcribe docs lists `hi/te/kn/ml/bn`
(+ `mr/gu/pa/or/as/sd/en-IN`) but **no `ta-IN`** — does the website brain actually support
Tamil, and what happens without a hint?

## Verdict

**Tamil works — both ways.** The table documents *hints*, not capability.

| call | config | result |
|---|---|---|
| A | auto-detect (`language_codes` omitted) + word timestamps | **Tamil ratio 1.00**, 22 `word_info` annotations, offsets `0.100s → 8.200s` in an 8.4 s clip |
| B | explicit hint `language_codes: ["ta-IN"]` (unlisted code) | **accepted** (no 400), same Tamil output |

Fixture: 8.4 s slice `[85s, 93.4s]` of a real Tamil reel (verified locally: 20 words,
tamil-script ratio 1.00; regions 0–6.4 s are dead air and 41–54 s makes the local small
model hallucinate, so neither is usable as ground truth).

Quality is **better than local faster-whisper `small`** on the same clip:

```
gemini : அது நம்ம ரியல் டைம்ல கேக்குற மாதிரி இருக்கும் …
local  : அது நம்ம ரில்ட்டையில் கேட்க்கும் மாறி இருக்கும் …   (mangled words)
```

## What this changes for the website

1. **No Tamil fallback needed.** The brain path is: `language_codes: []` (auto-detect,
   handles code-switching) or `["ta-IN"]` when the user names the language. Local
   faster-whisper stays as the offline/skill path only.
2. **Word timestamps are the cutlist clock.** `mode: {type:"verbatim",
   timestamp_granularities:["word"]}` → `steps[].content[].annotations[]` with
   `type=="word_info"`, `text`, `start_offset`/`end_offset` as strings like `"0.100s"`
   (parse: `float(v.rstrip("s"))`). These map 1:1 onto our `Word(t, s, e, conf)`.
   Note: timestamps ⇒ `verbatim` only (incompatible with `smart` mode), and they may
   slightly degrade accuracy (docs limitation) — acceptable, the cutlist needs the clock.
3. **`smart` mode is for reading, not editing** — it strips fillers, which our filler
   analyzer needs to see. Use verbatim.

## API gotchas found while probing (raw REST, no SDK)

- **`input[0].type` must be `"audio"`**, with `uri` + `mime_type`:
  `{"type":"audio","uri":"https://…/files/xxx","mime_type":"audio/mp3"}`.
  The docs' REST quickstart shows `{"type":"file","file_uri":…}` and is **stale** —
  the API rejects it: `The value 'file' is not supported for 'type' at 'input[0]'`.
- **Files API resumable upload** (2-step, raw REST):
  1. `POST /upload/v1beta/files` with headers `X-Goog-Upload-Protocol: resumable`,
     `X-Goog-Upload-Command: start`, `X-Goog-Upload-Header-Content-Length/-Type`,
     body `{"file":{"display_name":…}}` → read `X-Goog-Upload-URL` response header.
  2. `POST` raw bytes to that URL with `X-Goog-Upload-Command: upload, finalize`
     **and `X-Goog-Upload-Offset: 0`** (missing → `400 Missing X-Goog-Upload-Offset`).
  3. Response `file.uri` may be `PROCESSING`; poll `GET /v1beta/files/<name>` until
     `state == "ACTIVE"` (instant in practice for short clips).
- Request header `Api-Revision: 2026-05-20`, `store: false` (same as the export calls).

## Reproduce

```bash
cd ~/hyproai-oss
.venv/bin/python scripts/probe_transcribe_tamil.py     # exit 0 = PASS
```

Cost: one file upload + 2 transcription calls (~8 s audio each).
