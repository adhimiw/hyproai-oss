# Pipeline build spec (contract for all stage implementers)

Repo: `/home/adhithan/hyproai-oss` · Python: `/home/adhithan/hyproai-oss/.venv/bin/python`
Run everything from `/home/adhithan/hyproai-oss/core` (package root).

## Shared contracts (already implemented — import, don't reinvent)

- `hypro_core.timing` — `Word(t,s,e,conf)`, `Transcript` (+ `.save/.load`, `.gaps()`, `.text()`),
  `remap_to_record(words, keep)`. All times are source-timeline SECONDS.
- `hypro_core.media` — `probe_media(path) -> MediaInfo(duration, fps, w, h, sample_rate, has_audio, has_video)`.
- `hypro_core.cutlist` — `Cutlist`, `Clip(media, src_in, src_out, rec_in, name, role)`,
  `CaptionCue(start, end, text, native, words)`,
  `Cutlist.from_keep_ranges(source, duration, keep, **meta)` packs ranges back-to-back.
- `hypro_core.romanize` — `romanize(text, lang)` / `Romanizer.romanize_token(token, lang) -> (roman, source)`
  for hi/ta/te/ml/kn/bn.
- `hypro_core.export.export_nle(cutlist, out_dir, name)` — already done (XML/OTIO/SRT/JSX/EDL).

## Files each stage owns (DO NOT edit files outside your assignment)

- Agent A: `hypro_core/transcribe.py`, `hypro_core/analyze.py`
- Agent B: `hypro_core/captions.py`, `hypro_core/caption_presets/*.json`
- Agent C: `hypro_core/audio.py`, `hypro_core/render.py`

Do NOT run git commands, do NOT install packages (deps already installed:
faster-whisper 1.2.1, pysubs2 1.9.1, soundfile, ctranslate2, opentimelineio,
pandas; ffmpeg 9.0.2 with libass), do NOT touch `.venv`.

## Function signatures (the integration contract — exact names)

### Agent A — `transcribe.py`
```python
def extract_audio(video: str, out_wav: str | None = None, sr: int = 16000) -> str
def transcribe(video: str, *, lang: str | None = None, model: str = "small",
               word_timestamps: bool = True, device: str | None = None,
               initial_prompt: str | None = None) -> Transcript
    # ffprobe meta → faster-whisper (VAD on, word_timestamps) → Transcript
    # engine={"asr": "faster-whisper", "model": model}; fill language/fps/w/h/sr.
    # Segment-level confidence → per-word conf (segment avg is fine;1.0 if absent).
```

### Agent A — `analyze.py`
```python
def silence_keep_ranges(tr: Transcript, *, floor: float = 0.30,
                        merge_gap: float = 0.80,
                        margin: tuple[float, float] = (0.15, 0.35)
                        ) -> tuple[list[tuple[float, float]], list[dict]]
    # keep = complement of dropped; dropped entries:
    # {"start": s, "end": e, "reason": "silence", "gap": g}
    # Word-gap driven (tr.gaps()): drop gap if g >= merge_gap, keep `margin`
    # around neighbouring words (asymmetric: 0.15 before, 0.35 after),
    # then merge surviving keep pieces separated by < merge_gap.
    # Drop-burst rule: any kept piece shorter than `floor` is dropped too.

def retake_keep_ranges(tr: Transcript, *, jaccard: float = 0.55,
                       min_group_gap: float = 1.0
                       ) -> tuple[list[tuple[float, float]], list[dict]]
    # v1 deterministic, NO embeddings/models: split tr.words into sentence
    # groups on gaps >= min_group_gap; compare consecutive groups with
    # token Jaccard >= jaccard AND length ratio >= 0.5 → they are retakes of
    # the same line; keep the group with the higher score
    #   score = 0.6*mean(word conf) + 0.4*closeness( duration, median group dur )
    # tie within 5% → keep the LATER take; dropped entries reason="retake",
    # carrying {"kept_group": i, "drop_group": j, "score": x} for the audit.
    # Conservative: never drop a group that has no clear repeat partner.

def filler_word_ranges(tr: Transcript, *, min_dur: float = 0.30,
                       pad: float = 0.08) -> list[tuple[float, float]]
    # locale filler lexicon (en + hi/ta/te/ml/kn/bn chat fillers:
    # um uh er umm hm mm matlab toh acha yaar na ki hmm …), lowercased match;
    # drop only if dur >= min_dur; skip if within `pad` of a neighbouring
    # word boundary (Descript "avoid harsh cuts").

def build_cutlist(video: str, tr: Transcript, *, silence: bool = True,
                  retake: bool = False, fillers: bool = False,
                  **silence_kw) -> Cutlist
    # compose the enabled analyses: intersect keep ranges (union of drops),
    # Cutlist.from_keep_ranges, populate .dropped with all audit entries,
    # .fps/.width/.height/.sample_rate from tr, meta={"lang": tr.language}.
    # Word timing: leave cutlist.captions empty (captions stage fills it).
```

### Agent B — `captions.py`
```python
def group_cues(words: list[Word], *, max_chars: int = 42, max_words: int = 8,
               max_dur: float = 4.0, break_gap: float = 0.6
               ) -> list[CaptionCue]
    # greedy grouping on the RECORD timeline words (caller remaps): break on
    # char/word/dur budget or word gap >= break_gap; cue.words carries the
    # Word dicts {"t","s","e"}; cue.text = joined words (native for now).

def finalize_cues(cues: list[CaptionCue], lang: str | None) -> list[CaptionCue]
    # for each cue: cue.native = cue.text (pre-romanization); if lang:
    # romanize EVERY word in cue.words[i]["t"] via Romanizer.romanize_token
    # (1:1 token map keeps karaoke alignment) and cue.text = join of results.
    # Return new cues (don't mutate input).

def load_preset(name: str) -> dict            # reads caption_presets/<name>.json
def list_presets() -> list[str]
def compile_ass(cues: list[CaptionCue], preset: dict, *, width: int, height: int,
                out_path: str | None = None) -> str
    # pysubs2: convert record-time cues → ASS Dialogue events per the preset's
    # animation.mode (v1 REQUIRED modes: "karaoke_sweep" (\\k/\\kf centiseconds),
    # "word_pop" (duplicate Dialogue per word with \\t scale pop + \\fad),
    # "static"). Respect preset fields: font, size, colors (#RRGGBB → &HAABBGGRR),
    # bold, outline, shadow, alignment, margin_v, position/anchor, uppercase.
    # If cue.words present and mode=karaoke_sweep → one Dialogue with \\kf
    # per word, durations in centiseconds summing exactly to cue span
    # (largest-remainder allocation). Returns ASS text; writes out_path if given.

def burn(video: str, ass_path: str, out: str, *, crf: int = 18) -> str
    # ffmpeg -vf "ass=<ass_path>" single re-encode (libass). This is a
    # standalone convenience; the render stage also supports burning.
```

### Agent C — `audio.py`
```python
def enhance_ffmpeg(video: str, out: str, *, i: float = -16.0, tp: float = -1.5,
                   lra: float = 11.0) -> str
    # two-pass loudnorm + speech chain on the FULL media (duration unchanged):
    # afftdn=nr=15:nf=-45:tn=true → highpass=f=78 →
    # acompressor=threshold=-20dB:ratio=2.8:attack=20:release=250:makeup=4 →
    # deesser → firequalizer (gentle voice curve) → 2-pass loudnorm (measure
    # then apply, linear=true) → alimiter=limit=0.97. Re-mux video stream
    # COPY (-c:v copy) so it's fast and frame-identical.
    # Raises RuntimeError if input has no audio.

def analyze_loudness(audio_or_video: str) -> dict
    # ffmpeg loudnorm print_format=json measure pass → {"input_i", "input_tp",
    # "input_lra", "input_thresh"} floats.
```

### Agent C — `render.py`
```python
def render_edited(video: str, keep: list[tuple[float, float]], out: str, *,
                  ass: str | None = None, crf: int = 18,
                  preset: str = "medium") -> str
    # ONE ffmpeg filter_complex pass:
    #   per keep range: [0:v]trim,setpts=PTS-STARTPTS + [0:a]atrim,asetpts
    #   → concat=n=N:v=1:a=1 → (optional ass= burn AFTER concat: captions are
    #     on the RECORD timeline) → libx264 + aac.
    # Keep ranges are source-timeline; output duration == sum(range).
    # Handle no-audio inputs (video-only concat). Return output path.
    # MUST handle N up to ~200 ranges without exceeding filtergraph limits —
    # if the graph gets too long, fall back to: segment-wise trims into a
    # concat demuxer list, then a final ass+encode pass.
```

## Testing requirements (run these; paste results into your final report)

- **A**: `transcribe()` on a 45s cut of `/home/adhithan/Downloads/insta_reels/Janani ｜ Vocal⧸Veena Tutor_Dd9FDSsxDgH.mp4`
  (make the cut yourself with ffmpeg into `/tmp/opencode/stageA/`). Report detected
  language, word count, sample words, runtime. Then `build_cutlist(silence=True)`
  → print kept duration vs source duration + dropped reasons histogram.
  Sanity gate: keep ratio between 0.4 and 1.0, zero crashes.
- **B**: synthesize a `Transcript` fixture (20 words with realistic gaps, mix of
  Latin + Indic tokens), `group_cues` → `finalize_cues(lang="ta")` → `compile_ass`
  for at least 3 presets (one karaoke_sweep, one word_pop, one static) → assert
  (a) ASS parses with pysubs2, (b) karaoke centiseconds sum to cue span, (c) no
  native-script leak in finalized text when lang given. Then `burn()` on a 5s
  `testsrc2` clip you generate with ffmpeg → ffprobe duration must match input ±0.2s.
- **C**: generate a 20s test clip (testsrc2 + sine, with 3s of near-silence
  inserted) in `/tmp/opencode/stageC/`. `render_edited` with keep=[(0,8),(12,20)]
  → output duration ≈ 16s ±0.3. `enhance_ffmpeg` on it → runs, output exists,
  `analyze_loudness` before/after reports I ≈ -16 after.

## Style

- Python 3.10+, type hints, docstrings that state WHY (reference research doc
  numbers where relevant: `research/05-...` silence thresholds, `research/06-...`
  caption gotchas). No TODO stubs — every function must actually work.
- Fail loudly with readable errors (RuntimeError/FileNotFoundError), never bare except.
- Pure stdlib + already-installed deps only.
