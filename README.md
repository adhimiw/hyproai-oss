# hyproai-oss

Open-source AI video editor for Indian-language creators — a from-scratch,
permissively-licensed reimplementation of the core HyproAI idea: speak in
Tamil/Hindi/Telugu/…, cut the dead air, and caption it in the Roman letters
your audience actually reads (Thanglish, Hinglish, Telugish, Kanglish,
Malayalish, Benglish).

**Status:** core layers built. Skill and website (Next.js + Gemini API) next.

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
  cutlist.py    shared edit-decision contract (analyze → render → export)
research/       8 field research reports (ASR, romanization, silence/retake,
                captions, audio, NLE export, OSS landscape, product spec)
```

## Build & test

```bash
uv venv .venv && .venv/bin/pip install -e core   # + opentimelineio, ctranslate2, …
cd core
python -m hypro_core.romanize.eval_romanize --limit 400   # eval + leak gate
```

Lexicons are prebuilt (CC-BY-SA, see **NOTICE**); to rebuild from raw Dakshina
parquets, drop them in `romanize/data/raw/` and run `build_lexicon.py`.

## Licensing

Code: **MIT**. Dakshina-derived lexicon data: **CC-BY-SA-4.0** (share-alike,
attribution in `NOTICE`). No AGPL/NC components anywhere in the tree.
