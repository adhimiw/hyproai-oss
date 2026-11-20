# hyproai-oss

Open-source AI video editor for Indian-language creators — a from-scratch,
permissively-licensed reimplementation of the core HyproAI idea: speak in
Tamil/Hindi/Telugu/…, cut the dead air, and caption it in the Roman letters
your audience actually reads (Thanglish, Hinglish, Telugish, Kanglish,
Malayalish, Benglish).

**Status:** core pipeline + skill + website built and green — sidecar 43/43,
Next build clean, full-stack integration all PASS (incl. 30 MB upload
through the proxy and SSE through the rewrite).

## The two main layers

### 1. Thanglish layer — `core/hypro_core/romanize/`

Deterministic 4-layer cascade, Indic script → colloquial Latin, no LLM in the
hot path (µs–ms per token, fully offline after first run):

| Layer | What it does |
|---|---|
| L0 token gate | zero Indic codepoints → pass through verbatim (English stays English) |
| L1 glossary | user overrides — names, slang, brand spellings |
| L2 lexicon | 24k–45k attested Dakshina spellings per language |
| L3 rules + neural | per-language rule packs, then IndicXlit (CT2) for OOV with beam re-ranking |

**Held-out eval** (Dakshina test split, 1k sentences/language):

| lang | overall | in-vocab | OOV (neural) | native-script leak |
|---|---|---|---|---|
| hi | 95.4% | 100% | 45.6% | **0.00%** |
| te | 88.7% | 100% | 63.2% | **0.00%** |
| bn | 88.5% | 100% | 27.9% | **0.00%** |
| kn | 87.0% | 99.9% | 59.9% | **0.00%** |
| ta | 78.5% | 99.9% | 35.7% | **0.00%** |
| ml | 74.2% | 99.9% | 35.6% | **0.00%** |

```python
from hypro_core.romanize import romanize
romanize("வணக்கம் நண்பர்களே இன்று ஒரு புதிய வீடியோ", lang="ta")
# → 'vannakkam nanbarkalae indru oru puthiya veediyoa'
```

### 2. Export layer — `core/hypro_core/export/`

One `cutlist.json` (the diffable, git-friendly ground truth) → one folder:

```
edit.xml      FCP7 XML      → Premiere Pro + DaVinci Resolve (v17+)
edit.otio     open timeline → Resolve, future-proofing
edit.srt      captions      → universal sidecar (FCP7 XML has no subtitle track)
edit.jsx      After Effects → ExtendScript comp builder (no Premiere needed)
tracks/*.edl  CMX3600       → belt-and-braces, one per track
cutlist.json  ground truth  → fallback source if every import fails
HOWTO.txt     per-editor import instructions
```

Track layout: V1 = A-roll (jump cuts = separate clipitems), V2 = b-roll,
A1 = music, A2 = voice. Dropped ranges become review markers.

## Repo layout

```
core/hypro_core/
  romanize/     Thanglish layer (cascade, rules, lexicons, eval harness)
  export/       NLE export layer (OTIO → XML/EDL/SRT/JSX)
  transcribe.py analyze.py   word clock + silence/retake/filler cutlist
  captions.py audio.py render.py   ASS burn, loudness chain, ffmpeg render
  cutlist.py    shared edit-decision contract (analyze → render → export)
skill/SKILL.md  agent skill — give it a video, the pipeline runs
website/        Next.js UI + Python sidecar (API_CONTRACT.md is the spec)
scripts/        e2e smoke test, romanize eval, Gemini probes/experiments
research/       12 field research reports (ASR, romanization, silence/retake,
                captions, audio, NLE export, OSS landscape, product spec,
                website arch, Gemini export layer, Tamil transcribe)
```

## Build & test

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e core   # + faster-whisper, pysubs2, …
cd core
../.venv/bin/python -m hypro_core.romanize.eval_romanize --limit 400  # eval + leak gate
cd ..
.venv/bin/python scripts/e2e_smoke.py              # full pipeline end-to-end
```

Lexicons are prebuilt (CC-BY-SA, see **NOTICE**); to rebuild from raw Dakshina
parquets, drop them in `romanize/data/raw/` and run `build_lexicon.py`.

## Website (Next.js + Python sidecar)

`website/API_CONTRACT.md` is the binding spec between the two services.
The sidecar wraps the exact same `hypro_core` pipeline the CLI runs, with
Gemini (`gemini-3.5-transcribe`) as the ASR brain and local faster-whisper
as automatic fallback; romanization always runs locally.

```bash
# full stack in Docker (web :3000, sidecar :8787)
GEMINI_API_KEY=... docker compose -f website/docker-compose.yml up --build
# port 3000 busy? WEB_PORT=3012 docker compose -f website/docker-compose.yml up

# or run services directly for development
cd website/sidecar && ../../.venv/bin/python -m uvicorn app:app --port 8787
cd website/web && npm run dev        # separate terminal

# test gates
cd website/sidecar && ../../.venv/bin/python test_sidecar.py   # no key needed
cd website/web && npm run build                                  # TS strict
.venv/bin/python website/integration_test.py --brain local       # full stack
```

One transport detail: `/api/sidecar/*` is a Next rewrite, except
`POST /api/sidecar/uploads`, which is a streaming route handler
(`app/api/sidecar/uploads/route.ts`) because Next truncates rewritten
request bodies at 10 MB — a >10 MB video used to arrive at the sidecar as
a mid-stream disconnect and surface as a 500.

## Agent skill

`skills/hypro-edit/` is a cross-agent skill (Claude Code, OpenCode, Codex,
Antigravity). It probes the input video, asks for caption style/language
before running, then drives the CLI end-to-end and verifies the outputs.
Install everywhere with `npx skills add . --skill hypro-edit --agent '*' --copy -y`
(or copy the folder into your agent's skills directory). Evals and expected
behaviours are in `skills/hypro-edit/evals/evals.json` — the whole skill is
~5.4k tokens (cl100k_base), with details in `references/` loaded on demand.

## Licensing

Code: **MIT**. Dakshina-derived lexicon data: **CC-BY-SA-4.0** (share-alike,
attribution in `NOTICE`). No AGPL/NC components anywhere in the tree.
