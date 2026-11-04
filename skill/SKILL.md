---
name: hypro-edit
description: "AI video editor for Indian-language video — give it a video file and it runs the full pipeline: transcribe (word-timed), romanize to Thanglish/Hinglish/Telugish/Kanglish/Malayalish/Benglish, remove silence (and optionally retakes/fillers), enhance audio, burn styled karaoke captions, and export Premiere/Resolve XML + OTIO + SRT + AE JSX + EDLs. Use when the user says 'edit this video', 'remove the silences/dead air', 'make Thanglish/Hinglish captions', 'caption my Tamil/Hindi/Telugu/… reel', 'cut the retakes', 'clean up this audio', 'export to Premiere/Resolve/After Effects', or shares a video file to be edited. Works offline (local Whisper + rule/lexicon romanization); Gemini API not required."
argument-hint: "<video-file> [--lang ta|hi|te|ml|kn|bn] [--style preset] [--retake] [--fillers]"
allowed-tools: Read, Glob, Grep, Bash
---

# hypro-edit — run the open-source HyproAI pipeline on a video

Turns a raw recording into a finished edit: cut dead air, Thanglish-style
captions burned in, audio cleaned, and an NLE project folder for fine-tuning.

## Repo & invocation

Engine repo: `/home/adhithan/hyproai-oss` (if missing, ask the user where it is).

```bash
cd /home/adhithan/hyproai-oss/core
PY=/home/adhithan/hyproai-oss/.venv/bin/python
$PY -m hypro_core.cli edit <video> --lang <lang> --style <preset>
```

`--lang` is both the ASR hint and the romanization target:
`hi ta te ml kn bn`. Omit it to auto-detect (captions then stay
as-transcribed, no romanization).

## Standard run (defaults are sane)

```bash
$PY -m hypro_core.cli edit reel.mp4 --lang ta --style tiktok-karaoke
```

Output directory `<stem>_hypro/`:

| file | what |
|---|---|
| `<stem>_edited.mp4` | final edit: silences cut, captions burned, audio enhanced |
| `words.json` | word-timed transcript (reusable — see below) |
| `captions.ass` | the styled caption file that was burned |
| `audio_clean.mp4` | enhanced-audio intermediate |
| `cutlist.json` | every keep/drop decision + audit trail |
| `nle/` | `edit.xml` (Premiere/Resolve) · `.otio` · `.srt` · `.jsx` (After Effects) · `tracks/*.edl` · `HOWTO.txt` |

## Choosing options

- **Retake removal** (`--retake`): off by default; conservative lexical
  matcher — only drops a sentence-group when a near-identical repeat follows
  (Jaccard ≥ 0.55). Always review `cutlist.json` `dropped` entries after.
- **Filler removal** (`--fillers`): drops ≥0.3 s filler words (um, uh,
  matlab, toh, yaar, …).
- **Presets**: `$PY -m hypro_core.cli presets` lists them (hormozi-pop,
  tiktok-karaoke, mrbeast-bounce, minimal-fade, neon-glow, indic-karaoke, …).
  Use `indic-karaoke` when captions must render Indic script too.
- **No burn** (`--no-burn`): captions stay as `.srt`/`.ass` sidecars only.
- **Skip stages**: `--no-audio`, `--no-silence`, `--no-nle`, `--no-captions`.
- **Long videos / slow GPU**: pass `--model medium` (better Indic accuracy)
  or `--model base` (faster). First run downloads the model (~150 MB–1.5 GB).

## Workflow tips

1. **Run `--dry-run` first on unknown footage** — prints the keep-ratio and
   dropped-reason histogram without rendering. Keep-ratio < 40 % or > 100 %
   means thresholds need tuning.
2. **Reuse the word clock**: transcription is the slow step. Re-runs with
   `--words-json <dir>/words.json` skip it entirely (different cut settings,
   different preset, no re-transcription).
3. **Single stages** are available: `transcribe`, `romanize` (text →
   Thanglish on stdin), `presets`, `export` (re-export NLE folder from an
   existing `cutlist.json`).
4. **Thanglish quality**: the romanizer is a 4-layer cascade (glossary →
   24–45k-word attested lexicon → rules + neural OOV). If a name/brand gets
   mistyped, fix it once with a glossary JSON (`--glossary mine.json`,
   `{"நண்பர்களே": "nanbargale"}`) — it wins over everything.
5. **After-effects users**: run the `nle/*.jsx` inside AE (File ▸ Scripts ▸
   Run Script File) — it rebuilds the comp without Premiere. Everyone else
   imports `nle/edit.xml`; see `nle/HOWTO.txt` for per-editor steps.

## Verification (do this before telling the user it worked)

```bash
ffprobe -v error -show_entries format=duration -of csv=p=0 <stem>_edited.mp4
# duration ≈ sum of kept ranges, NOT the source duration
head -20 <stem>_hypro/nle/<stem>.srt   # romanized text, sane timings
python - <<'EOF'
import json; c=json.load(open("<stem>_hypro/cutlist.json"))
print(len(c["tracks"]["video"]), "clips,", len(c.get("dropped",[])), "drops,",
      len(c.get("captions",[])), "cues")
EOF
```

If transcription produced garbage for the language (accent/slang), retry with
`--model medium`; if a specific word is wrong everywhere, glossary it.

## Troubleshooting

- `ffprobe not found` → install ffmpeg (needs libass for caption burn).
- CUDA/ctranslate2 error → the CLI auto-falls back to CPU; expect ~4× slower.
- `unknown preset` → run `hypro presets`.
- Everything dropped → run `--dry-run`, then relax with `--no-silence` to
  isolate which analyzer is over-cutting.
