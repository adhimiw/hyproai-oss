# 10 — Website architecture: Next.js full-stack + Gemini API

Status: **draft for the website stage** (core → skill → website was the agreed
build order). Decisions marked ✅ are settled; ❓ need a user call before build.

## 1. What the Gemini API gives us (verified 2026-11)

### `gemini-3.5-transcribe` (Interactions API / generateContent) — the ASR brain
- **Word-level timestamps**: `timestamp_granularities: ["word"]` (Interactions)
  or `word_timestamp: true` (generateContent) → per-word `start_offset/end_offset`
  — exactly our `words.json` contract. ⚠️ degrades WER slightly; incompatible
  with `custom_vocabulary`; 30-min cap when timestamps are on (1 h without).
- **85+ languages with mid-session code-mixing** — built for the
  code-switched Indian speech that breaks single-aligner pipelines (our #1
  local ASR risk, research 03).
- **Smart mode**: removes fillers *and repetitions* ("ums", self-corrections)
  with structured output — a second opinion for our filler/retake analyzers.
  Smart is mutually exclusive with word timestamps + diarization →
  run verbatim+timestamps as the source of truth, smart as an advisory pass.
- Diarization up to 8 speakers (podcast/multicam later).
- Limitations: live/streaming path has NO word timestamps; pricing is per-minute
  (check pricing page at build time).

### General Gemini flash — the romanization/analysis brain
- Native-script → Thanglish per word (1:1 token map keeps karaoke alignment),
  retake/keep-drop reasoning, caption style suggestions.
- Local cascade still ships in the web app for in-vocab words (lexicons are
  plain JSON — 11 MB, portable to the browser): **TS cascade L0/L1/L2 is free
  and instant; only OOV words cost a Gemini call** (batched).

## 2. The real architectural fork

The Python core (cutlist, ASS compiler, OTIO/XML export, ffmpeg) does not run
on Vercel-style serverless. Three honest options:

| | A. TS port + ffmpeg.wasm (browser) | B. Next.js + Python sidecar (Docker/VPS) | C. Hybrid |
|---|---|---|---|
| Deploy | Vercel/GH Pages, free | VPS ~$5/mo, Docker compose | VPS, same as B |
| Video leaves the machine | No — renders in browser (privacy win vs HyproAI) | Yes — uploaded to your server | Yes |
| Long videos (40-min podcast) | Slow (wasm ~0.5–2× realtime), 2–4 GB WASM mem limits | Full speed, real ffmpeg + optional local Whisper | Same as B |
| Code sharing with skill | Lexicons + cutlist JSON + preset JSON shared; ASS/XML writers duplicated in TS | **100 % — same Python package** | Same as B |
| Effort | Medium (ASS compiler + FCP7 XML writer in TS) | Low (HTTP wrapper around existing core) | Medium |

❓ **Open question for the user:** deploy target.
- **A** = free hosting, private, demo-able, but slow on long videos.
- **B** = one `POST /api/edit` wrapping the exact code we just tested; fastest
  to ship, matches "full-stack Next.js" literally (API routes are the
  full-stack part), needs a $5 VPS or Docker on a home server.
- **C** = B's API for heavy lifting + an in-browser preview renderer.

**My recommendation: B first, A as the "privacy mode" follow-up.** The whole
point of Core → Skill → Website was code reuse; B reuses the Python core 1:1
and ships the website in days, not weeks.

## 3. Recommended pipeline (option B)

```
Browser (Next.js client)                Server (Next.js API routes → Python core)
───────────────────────                 ────────────────────────────────────────
upload / drag video ──── multipart ───►  store temp file
pick lang + style                         │
timeline preview ◄──── SSE progress ──────┤ 1. probe_media
                                          │ 2. Gemini: gemini-3.5-transcribe
                                          │    (word timestamps) → words.json
                                          │    [fallback: local faster-whisper]
                                          │ 3. Gemini flash: OOV romanization
                                          │    + TS-style glossary overrides
                                          │ 4. analyze: silence/retake/filler → Cutlist
                                          │ 5. captions: group + finalize → ASS (pysubs2)
                                          │ 6. audio: enhance_ffmpeg
                                          │ 7. render_edited (+ass burn)
                                          │ 8. export_nle → zip
edit decisions on timeline ── PATCH ────► re-render from words.json (no re-transcribe)
download mp4 / srt / nle.zip ◄──────────  static/ or signed URL
```

- Re-runs are cheap: `words.json` is cached per upload; changing cut settings
  or caption style skips steps 2–3 (the same `--words-json` optimization the
  CLI already has).
- Gemini key: user-supplied (`GEMINI_API_KEY` env or in-app field stored in
  localStorage) for self-hosters; server key for our hosted demo.
- SSE (not websockets) for progress — one-directional, works on serverless-ish
  hosts.

## 4. Shared artifacts between skill, website, and CLI

| artifact | Python core | website |
|---|---|---|
| `lexicon_*.json` (CC-BY-SA) | ✓ | ✓ served as static (11 MB, lazy-loaded per lang) |
| `words.json` / `cutlist.json` contracts | ✓ `timing.py` / `cutlist.py` | ✓ TS types with identical field names |
| caption preset JSONs | ✓ | ✓ — same files, frontend preview reads them |
| ASS compiler | ✓ pysubs2 | TS port needed only for browser preview; authoritative burn stays server-side (option B) |
| FCP7 XML / SRT / EDL export | ✓ OTIO | SRT trivial in TS; XML stays server-side (option B) |

## 5. Open items before build

1. ❓ Deploy target (A / B / C above) — user decision.
2. Pricing check for `gemini-3.5-transcribe` per-minute (budget the demo).
3. Upload limits: v1 = ≤10 min / ≤500 MB (match HyproAI's free tier shape);
   chunked upload + Files API for Gemini.
4. Auth: skip for v1 (rate-limit by IP) or Supabase phone-OTP (OSS parity
   pick from research 01)? — v1: skip, add before public launch.
