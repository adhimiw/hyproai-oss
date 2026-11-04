# 11 — Can Gemini do the export layer? (empirical)

**Date:** 2026-11-04 · **Method:** `scripts/gemini_export_experiment.py` (checked in, re-runnable)
**Question:** should the NLE export layer (FCP7 XML / SRT / EDL / cutlist.json) be generated
by deterministic code, by Gemini, or both?

## Verdict

**Gemini can do it — every call that reached a model passed (16/16 validations).**
But deterministic code remains the primary exporter; Gemini is validated as the
**fallback/export-from-browser path** and as the **brain** upstream of the cutlist.

| | deterministic (`hypro_core.export.nle`) | Gemini (flash models) |
|---|---|---|
| correctness | exact by construction | **16/16 pass** frame-exact validators |
| latency | < 1 s | 11–43 s typical, **379 s worst** (cold XML) |
| cost | free | ~0.6–8.4 K tokens/call, free tier = **20 req/day** |
| verifiable | byte-diffable in git | needs the validator harness |
| offline | yes | no |
| quota/failure | none | 429/503 common, retries mandatory |

## Test design

One fixture cutlist → four artifacts, each validated **frame-exact** (not eyeballed):

| test | input | validator |
|---|---|---|
| `srt` | caption cues (Thanglish, Unicode) | `pysubs2` parse, ±1 ms, verbatim text |
| `xml` | cutlist JSON | XML parse, sequence duration/timebase/ntsc, every `start/end/in/out` frame == `round(sec·30)`, `pathurl` id-ref resolution |
| `edl` | clip list | CMX3600 regex, all 4 timecodes/event == independent `HH:MM:SS:FF` math |
| `cutlist` | keep-ranges + dropped | **structured output** (JSON Schema), all fields ±1e-3, dropped verbatim |

- Fixture is **frame-exact by construction** (defined in frames, converted to seconds) so
  ground truth has no rounding ambiguity; our own exporter runs through the same validators
  as a sanity check first (`check_fixture_math`), so a bug in *our* exporter can't mask one in Gemini's.
- Every prompt carries the full format spec **plus one worked micro-example** — a failure
  means "can't do the arithmetic even when guided".
- Ground truth computed independently in the harness; Gemini's raw output kept in `tmp/gemini_probe_raw*/`.

## Results

| model | scale | srt | xml | edl | cutlist | notes |
|---|---|---|---|---|---|---|
| `gemini-3.5-flash` | 4 clips | ✅ | ✅ | ✅ | ✅ | XML 379 s cold, then 43 s warm |
| `gemini-3.5-flash` | 4 clips (repeat) | ✅ | ✅ | ✅ | ⚠️ 429 | quota, not correctness |
| `gemini-3.5-flash` | **16 clips** | ✅ | ✅ | ✅ | ✅ | 3342-tok XML, all 64 frames right |
| `gemini-3.6-flash` | 4 clips | ✅ | ✅ | ✅ | ✅ | fastest: 11–26 s |
| `gemini-3.6-flash` | 16 clips | ⚠️ 429 | ⚠️ 429 | ⚠️ 429 | ✅ | daily quota hit |
| `gemini-4-argon` | — | ❌ 429 | ❌ 429 | ❌ 429 | ❌ 429 | **0 tokens/min on Free Tier** |
| `gemini-3.7-flash` / `3.8-flash` | — | ❌ | ❌ | ❌ | ❌ | 20 req/day shared family quota |
| `gemini-2.5-flash` | — | ❌ 404 | ❌ 404 | ❌ 404 | ❌ 404 | retired for new users |

**Correctness: 16/16 on every call that reached a model. Zero arithmetic, timing, or
structure errors at 4 clips and at 16 clips (64 frame values + 16 timecode pairs each).**
All FAILs are transport/quota (`429/503/404`), never a bad artifact.

Sample (3.6-flash, EDL — independently verified):

```
TITLE: hypro_edit
FCM: NON-DROP FRAME
001  cut_0000  V  C  00:00:02:03 00:00:06:13 00:00:00:00 00:00:04:10
002  cut_0001  V  C  00:00:09:23 00:00:14:02 00:00:04:10 00:00:08:19
...
```

## What this changes in the architecture

1. **Primary exporter stays deterministic.** Sub-second, free, offline, git-diffable.
   Gemini is slower, metered, and needs a validator to trust — for a batch pipeline that
   runs on every edit, code wins.
2. **Gemini = validated fallback.** The website can offer "export without the Python
   sidecar": feed the cutlist to `gemini-3.6-flash` with the spec prompts (this script's
   prompts are the prototype) and gate output through `validate_*`. This degrades the
   sidecar-B architecture gracefully instead of hard-requiring ffmpeg on the VPS.
3. **Gemini's real job is upstream** — transcribe (`gemini-3.5-transcribe`), OOV
   romanization, semantic analysis (what to cut). That's where judgment beats code;
   the export layer is pure arithmetic, where code beats judgment.
4. **Structured output works and is the right tool for `cutlist.json`** — schema-constrained
   generation returned valid, in-range JSON on every attempt (docs:
   `aistudio.google.com/docs/structured-output`, Interactions API `response_format.schema`).

## Limitations / next tests

- Prompts include worked examples; a spec-less test (raw "make me an FCP7 XML") would
  likely fail — but any real integration ships the spec anyway, so this measures the
  deployable configuration.
- Free tier (20 req/day flash family, argon = 0) caps throughput; a production fallback
  path needs the paid tier. Cost still trivial per export (~few K tokens).
- 30 fps non-drop only; 29.97 drop-frame and 25 fps (common for Indian TV deliverables)
  are untested — add fixtures before claiming them.
- Repeat-run variance: only one content repeat per model; more passes would bound flake rate.

## Reproduce

```bash
cd ~/hyproai-oss
.venv/bin/python scripts/gemini_export_experiment.py                      # base, 2 models
.venv/bin/python scripts/gemini_export_experiment.py --scale 16           # 16-clip stress
.venv/bin/python scripts/gemini_export_experiment.py --models gemini-3.6-flash --tests xml
```

Exit code 0 = all PASS. Raw outputs + `tmp/gemini_probe_results_*.json` for the record.
