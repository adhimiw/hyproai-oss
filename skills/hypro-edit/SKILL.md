---
name: hypro-edit
description: "AI video editor for Indian-language video — give it a video file and it runs the full open-source HyproAI pipeline: word-timed transcription, Thanglish/Hinglish/Telugish/Kanglish/Malayalish/Benglish romanization, silence/retake/filler removal, audio cleanup, styled karaoke captions burned in, and NLE export (Premiere/Resolve FCP7 XML, Final Cut FCPXML, OTIO, AE JSX, EDLs, SRT). Use when the user says 'edit this video', 'remove the silences', 'make Thanglish captions', 'caption my Tamil/Hindi/Telugu reel', 'cut the retakes', 'burn karaoke captions', 'export to Premiere/Resolve/Final Cut', asks about caption presets or custom fonts, or shares a video file. Works offline; Gemini optional."
argument-hint: "<video-file> [--lang ta|hi|te|ml|kn|bn] [--style preset]"
allowed-tools: Read, Glob, Grep, Bash
---

# hypro-edit — run the HyproAI pipeline on a video

Turns a raw recording into a finished edit: dead air cut, Thanglish-style
captions burned in, audio cleaned, and an NLE folder for finishing.
Engine: the repo this skill ships in (`skills/hypro-edit/` inside
**hyproai-oss**). Commands below run from the repo root.

Offline-first: local Whisper + rule/lexicon romanization need no API key.

## The mandatory workflow

### Step 0 — locate the CLI

```bash
hypro --help                       # installed via: pip install -e core
PYTHONPATH=core .venv/bin/python -m hypro_core.cli --help   # otherwise
```
`hypro` below means either form.

### Step 1 — ANALYSE the input before anything else

```bash
bash skills/hypro-edit/scripts/probe.sh <video>
```
(or plain `ffprobe`: duration, resolution, fps, video/audio codecs,
has_audio). Report to the user: length, format, whether audio exists,
and an estimate of whether the file suits the pipeline.)

### Step 2 — ASK before running (mandatory)

Do **not** run the pipeline until the user has confirmed these. Ask with
one question block; skip anything they already specified in their message:

1. **Caption style** — offer the 8 presets with one-line vibes:
   `tiktok-karaoke` (sweeping yellow highlight, 4-word lines),
   `hormozi-pop` (big popping words), `mrbeast-bounce` (huge uppercase),
   `indic-karaoke` (sweep tuned for Indic scripts), `slide-up` (lines
   slide in), `neon-glow` (cyan/magenta glow), `boxed-classic` (white on
   black box), `minimal-fade` (quiet, no flash). Details:
   `references/presets-and-fonts.md`.
2. **Language / romanization** — `ta|hi|te|ml|kn|bn` for Thanglish-style
   romanized captions, or native script (no romanization), or English.
   If unsure after probing, offer auto-detect.
3. **What to cut** — silence only (default) / also retakes / also filler
   words; and whether to burn captions into the video or ship a sidecar
   SRT only.
4. **Finishing target** — just the MP4, or an NLE project (Premiere /
   DaVinci / Final Cut / After Effects). If NLE: see Step 5.

If the user answered all of these up front, confirm the plan in one line
and proceed — never re-ask. If they asked something the skill cannot do
(e.g. translate), say so instead of guessing.

### Step 3 — run the pipeline

```bash
# full edit (probe → transcribe → analyze → captions → audio → render → NLE)
hypro edit <video> --lang ta --style tiktok-karaoke --out ./edited/

# common flags: --retake  --fillers  --no-burn  --no-silence  --no-audio
#              --no-nle  --model small|medium|large-v3  --dry-run
```
`--dry-run` prints the plan (keep ranges, captions, preset) without
rendering — show it to the user if they want to preview the cut.

Reuse an existing transcript instead of re-transcribing:
`hypro edit <video> --words-json edited/words.json ...` (rerender with a
new style costs seconds, not minutes).

### Step 4 — verify the outputs (never skip)

```bash
ffprobe -v error -show_entries format=duration -of csv=p=0 edited/<name>_edited.mp4
grep -c '' edited/captions.srt          # cue count > 0
ls edited/nle/                          # xml, fcpxml, otio, srt, jsx, edl…
```
Check: edited duration ≈ sum of kept ranges (shorter than source means
cuts happened), SRT has cues, NLE folder is complete. Report the numbers
you measured — duration kept, cue count, preset used.

### Step 5 — NLE export

`hypro edit` writes `nle/` automatically (disable with `--no-nle`). To
export from an existing cutlist: `hypro export cutlist.json --out nle/`.
Artifacts and per-editor import steps: `references/nle-export.md`
(Premiere/Resolve = `.xml`, Final Cut = `.fcpxml`, AE = `.jsx`,
plus `.otio`, per-track `.edl`, `HOWTO.txt`).

## Custom fonts & presets

Style JSONs live in `core/hypro_core/caption_presets/*.json` (copy one,
edit, keep `id` == filename). Fonts resolve via fontconfig at burn time
(`fontFamily` must be installed on the machine, else the `fontFallback`
chain). Full schema + authoring guide: `references/presets-and-fonts.md`.

## Reference index (read only what the task needs)

- `references/presets-and-fonts.md` — the 8 presets, schema, custom fonts
- `references/nle-export.md` — export artifacts + import recipes
- `references/cli-and-api.md` — every CLI subcommand + website HTTP API

## Install (cross-agent)

From the repo root — one command, all agents:

```bash
npx skills add . --skill hypro-edit --agent '*' --copy -y
```

Manual placement (if you don't use the `skills` CLI): copy
`skills/hypro-edit/` to `~/.claude/skills/` (Claude Code),
`~/.config/opencode/skills/` (OpenCode), `~/.agents/skills/`
(Antigravity/agy + Codex-compatible), then restart the agent.

## Troubleshooting

- **`ffprobe/ffmpeg not found`** → install ffmpeg (apt/brew); the whole
  pipeline is ffmpeg-based.
- **Empty transcript** → the local `small` model may miss noisy audio;
  retry `--model medium`. Gemini brain: set `GEMINI_API_KEY` (see
  `references/cli-and-api.md`).
- **Weird glyph boxes in captions** → the preset font isn't installed;
  install it or pick a preset whose `fontFallback` covers the script
  (Noto Sans Tamil/Devanagari…).
- **Need to actually WATCH/understand the video content** (not just edit
  it) → use the `watch` skill for frames/transcript; for raw ffmpeg
  filter questions, the `ffmpeg` skill.
