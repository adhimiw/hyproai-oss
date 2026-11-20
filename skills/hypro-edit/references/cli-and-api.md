# CLI & HTTP API reference

## CLI (`hypro` — or `PYTHONPATH=core .venv/bin/python -m hypro_core.cli`)

### `hypro edit <video>` — the full pipeline
| flag | effect |
|---|---|
| `--lang hi\|ta\|te\|ml\|kn\|bn` | romanization target (Thanglish layer); omit = native script |
| `--style <preset>` | caption preset id (`hypro presets`) |
| `--model tiny\|base\|small\|medium\|large-v3` | local whisper (default small) |
| `--no-captions` / `--no-burn` | no captions / sidecar SRT only, don't burn |
| `--no-silence` | keep everything (no dead-air cut) |
| `--retake` / `--fillers` | also cut retakes / filler words (conservative) |
| `--no-audio` | skip denoise+compress+loudnorm |
| `--no-nle` | skip the NLE folder |
| `--words-json <path>` | reuse an existing transcript (skips transcription) |
| `--out <dir>` | output dir (default `<input>_hypro/`) |
| `--crf <n>` | x264 quality · `--dry-run` plan only |

Outputs: `<name>_edited.mp4`, `captions.srt`, `captions.ass`,
`cutlist.json`, `words.json`, `nle/`.

### Other subcommands
- `hypro transcribe <video> [--lang ta] [--model small] [-o words.json]`
- `hypro romanize [--lang hi] [--glossary glossary.json]` — text on stdin;
  glossary = `{native: latin}` overrides for names/brands
- `hypro presets` — list style ids
- `hypro export <cutlist.json> --out <dir> [--name x]` — re-export NLE only

## Website HTTP API (sidecar, default `http://localhost:8787`)

Flow: `POST /uploads` (multipart `file`, ≤500 MB / ≤600 s) → 201
`{id, duration, has_audio, …}` · `POST /jobs` `{upload_id, lang, style,
options:{silence, retake, fillers, burn, enhance_audio, brain, model}}`
→ 202 `{id}` · poll `GET /jobs/{id}` or stream `GET /jobs/{id}/events`
(SSE: progress/log/done/error) · download `GET /jobs/{id}/files/<name>`
(edited.mp4, captions.srt, captions.ass, nle.zip, cutlist.json,
words.json). `GET /health` (reports `gemini_key`), `GET /presets`.
Re-render with a new style cheaply: `POST /jobs {reuse_words_of: <id>}`.
Full contract: `website/API_CONTRACT.md`.

**Brains**: `gemini` (needs `GEMINI_API_KEY`; word timestamps + captions
via Gemini, falls back to local on 429 with an SSE `log` event) or
`local` (faster-whisper, fully offline).
