# 06 — Animated captions: generation, styling, burning

**Research date:** 2026-11-04 · **Scope:** open-source stack for word-timed, CapCut/viral-style
animated captions (generate → style → hard-burn) for a HyproAI clone, plus the portable preset
format for 12 shipped styles and correct sidecar exports (SRT/VTT/TXT).

**Method:** ~20 web searches + primary-source README fetches (whisperX, faster-whisper,
pysubs2, ai-video-captions, tscaps, MoneyPrinterTurbo, awesome-ai-video-for-creators), plus a
**local verification run** on this machine (ffmpeg n9.0.2 with `--enable-libass
--enable-libharfbuzz`, pysubs2 1.9.1): words → grouping → ASS/SRT/VTT/TXT → `ffmpeg -vf
ass=…` burn → frame extracted and visually confirmed the `\kf` karaoke sweep mid-word.
Artifacts: `/tmp/opencode/cap_test/` (`build_captions.py`, `captions.ass`, `out.mp4`,
`frame_karaoke.png`, `pop.ass`, `slide.ass`).

---

## 0. Executive summary

| Stage | Pick | License | Why |
|---|---|---|---|
| Generation (word timings) | **faster-whisper** (+ optional **whisperX** alignment) | MIT / BSD-2 | 25.7k★ active, `word_timestamps=True`, CTranslate2 fast, offline, pip-installable |
| Style format | **ASS (v4+)** built with **pysubs2** from **JSON presets** | MIT / MIT | karaoke (`\kf`), transforms (`\t`), `\move`, `\fad`, per-word events; text file = trivially portable |
| Renderer/burn | **ffmpeg `ass=` filter → libass → HarfBuzz** | ffmpeg (GPL build) + libass | single-pass with encode, verified karaoke, full Indic/complex shaping, fastest hard-burn |
| Sidecars | same `group()` → **SRT + VTT + TXT + words.json** | — | cue boundaries identical to burned lines; word-level kept in JSON/VTT |

Avoid: **Remotion** (company license, paid >3 seats), **pysrt** (GPL-3.0), **aeneas**
(AGPL-3.0), **MMS aligner weights** (CC-BY-NC), **stable-ts** (archived), **tscaps studio**
(AGPL-3.0). Details in §6.

---

## 1. ASR caption generation (video/audio → word timings → SRT/VTT)

### 1.1 Candidate matrix

| Name | Repo | License | Stars / maintenance | What it does | Install |
|---|---|---|---|---|---|
| **faster-whisper** | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | **25.7k★**, active (v1.2.1, Oct 2025) | CTranslate2 re-impl of Whisper, 2–4× faster, `word_timestamps=True`, Silero VAD | `pip install faster-whisper` |
| **whisperX** | [m-bain/whisperX](https://github.com/m-bain/whisperX) | BSD-2-Clause | **24.4k★**, active (+~20★/day; context-aware batching 2026) | batched faster-whisper + **wav2vec2 forced alignment** → sub-100 ms word timings, diarization | `pip install whisperx` (or `whisperx[fast]` for numba) |
| **openai-whisper** | [openai/whisper](https://github.com/openai/whisper) | MIT | ~76k★, slower cadence | reference impl; CLI `--output_format srt --word_timestamps True` | `pip install -U openai-whisper` |
| **stable-ts** | [jianfch/stable-ts](https://github.com/jianfch/stable-ts) | MIT | 2.3k★, **ARCHIVED / paused** | `result.to_srt_vtt() / to_ass() / to_txt()`, word-level VTT output | `pip install stable-ts` |
| **aeneas** | [aeneas-team/aeneas](https://github.com/aeneas-team/aeneas) | **AGPL-3.0** ❌ | low activity | forced alignment of a *known* transcript against audio (no ASR) | `pip install aeneas` |
| MMS forced aligner (Meta) | HF `facebook/mms-*` aligner weights | **CC-BY-NC** ❌ | research | aligns text↔audio for many languages | — |

### 1.2 Details

#### (a) faster-whisper — **primary generator**

```python
from faster_whisper import WhisperModel

model = WhisperModel("large-v3", device="cpu", compute_type="int8")   # GPU: device="cuda", compute_type="float16"
segments, info = model.transcribe(
    "input.mp4",
    word_timestamps=True,          # <- the whole point
    vad_filter=True, vad_parameters={"min_silence_ms": 500},
    beam_size=5,
)
# segments is a generator: consume it exactly once
words = [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3),
          "p": round(w.probability, 3)}
         for seg in segments for w in (seg.words or [])]
```

- **Pros:** MIT, offline, fastest CPU/GPU option, word timestamps good enough for karaoke,
  Silero VAD kills hallucinations on silence, tiny dependency surface (ctranslate2, tokenizers).
- **Cons:** no SRT/VTT writer of its own for word-level output (repo ships `write_srt` for
  *segment* level in `whisper_from_segments.py` — you hand-roll sidecars from `words`);
  word timings are Whisper's, not forced-aligned → drift of tens of ms on fast/ accented speech.
- **Gotcha:** `transcribe()` returns a lazy generator — if you iterate twice you get nothing
  the second time. Materialize to a list on first pass.

#### (b) whisperX — **alignment upgrade when timings must be frame-accurate**

```python
import whisperx

audio = whisperx.load_audio("input.mp4")
model = whisperx.load_model("large-v3", device="cuda", compute_type="float16")
res = model.transcribe(audio, batch_size=16, interleaved_context=True)   # faster-whisper backend

am, meta = whisperx.load_align_model(language_code=res["language"], device="cuda")
res = whisperx.align(res["segments"], am, meta, audio, device="cuda",
                     return_char_alignments=False)
# res["segments"][i]["words"] == [{"word": "hello", "start": .., "end": .., "score": ..}]
```

- **Pros:** sub-100 ms word accuracy via wav2vec2 forced alignment (vs ~1 s drift for raw
  whisper segments); BSD-2; also gives diarization; batched = 70× realtime with large-v2.
- **Cons:** needs torch + per-language align models; **words absent from the aligner
  dictionary get no timing at all** (README: `"2014."`, `"£13.60"`) → must drop or interpolate
  those tokens; heavier install; GPU recommended (CPU works with `--compute_type int8`).
- **Output formats:** CLI writes SRT/VTT/TSV/JSON; `--highlight_words True` marks word timings
  **inside the SRT** (as inline markers, not renderable karaoke). **`.ass` output was removed
  in v3** (README TODO: "bring this back") — so you always build ASS yourself. ✔ that is our plan.

#### (c) openai-whisper — baseline

```bash
whisper input.mp4 --model large-v3 --output_format all --word_timestamps True --output_dir out/
```
- **Pros:** one command, `all` gives txt/vtt/srt/tsv/json; MIT.
- **Cons:** ~4× slower than faster-whisper; the emitted SRT/VTT are **segment-level** — word
  timestamps only exist in `words.json`. Fine as a fallback, not the default.

#### (d) stable-ts — feature-rich but frozen

```python
import stable_whisper
model = stable_whisper.load_model("large-v3")
result = model.transcribe_stable("input.mp4", word_timestamps=True)
result.to_srt_vtt("out.srt")     # also .to_ass(), .to_txt(); VTT can carry word timestamps
```
- **Pros:** cleanest one-liner for multi-format export; MIT; can emit **word-level VTT**
  (`word<00:00:07.860>` inline tags).
- **Cons:** repo is **archived/paused** — do not build a product dependency on it. Use it only
  as a reference for how word-level VTT should look.

#### (e) aeneas — do not use
AGPL-3.0 (network use triggers source disclosure → disqualifying for a hosted clone), and it
aligns a *pre-written* transcript rather than transcribing. Same for Meta's MMS aligner
weights (CC-BY-NC).

### 1.3 Subtitle file libraries (read/write sidecars)

| Lib | License | Stars | Formats | Verdict |
|---|---|---|---|---|
| **pysubs2** | MIT | ~440★ | **ASS/SSA, SRT, WebVTT, TTML, SAMI, MPL2, MicroDVD, JSON, Whisper outputs** | ✅ **use this** — the multi-format hub |
| **srt** (cdown) | MIT | ~535★ | SRT only | ✅ fine if you only need SRT |
| **webvtt-py** | BSD-3 | ~100★ | WebVTT only | ✅ fine for VTT-only |
| **pysrt** | **GPL-3.0** ❌ | ~500★ | SRT only | ❌ avoid (GPL on your codebase) |
| **aeneas** | **AGPL-3.0** ❌ | — | alignment | ❌ avoid |

`pysubs2` alone covers every sidecar we need plus ASS authoring, so the stack stays at one
subtitle dependency.

### 1.4 Canonical word-timing model (single source of truth)

Everything downstream (ASS, SRT, VTT, TXT, preview) derives from one structure:

```json
{
  "language": "en",
  "generator": "faster-whisper large-v3 + whisperx.align",
  "lines": [
    { "start": 0.10, "end": 1.12,
      "words": [ {"w": "Let", "s": 0.10, "e": 0.34},
                 {"w": "me",  "s": 0.34, "e": 0.52},
                 {"w": "show","s": 0.52, "e": 0.90},
                 {"w": "you", "s": 0.90, "e": 1.12} ] }
  ]
}
```

---

## 2. Animated / burned styling

### 2.1 Why ASS (Advanced SubStation Alpha) is the format

- It is a **plain text** format designed in the 2000s for fansubbing, where **syllable-timed
  karaoke was the entire point** — `\k`/`\kf` have existed for two decades.
- **libass** (the renderer behind ffmpeg's `ass`/`subtitles` filters) does full **OpenType
  shaping via HarfBuzz** → Devanagari/Gurmukhi/Tamil/Arabic/Hebrew join and reorder correctly.
  Verified on this machine: `ffmpeg -buildconf` shows `--enable-libass --enable-libharfbuzz`.
- One text file + one ffmpeg flag = burn-in. No browser, no per-frame Python, no GPU needed.
- Every "viral caption style" (Hormozi pop, MrBeast bounce, TikTok sweep) is expressible with:
  karaoke tags + per-word duplicated Dialogue events + `\t` transforms + `\move` + `\fad` + `\blur`.

### 2.2 Tag reference (what each preset uses)

| Tag | Effect | Used for |
|---|---|---|
| `\k<cs>` | instant color flip after *cs* centiseconds | hard karaoke |
| `\kf<cs>` / `\K<cs>` | **left-to-right fill sweep** over *cs* | TikTok/Reels sweep (verified render) |
| `\ko<cs>` | outline-only until highlighted | outline karaoke |
| `\t(a,b, …)` | transform (scale/position/color) between a–b ms | **pop/bounce** `\t(0,90,\fscx118\fscy118)` |
| `\move(x1,y1,x2,y2)` | move over event lifetime | slide-up captions |
| `\pos(x,y)` + `\an5` | absolute position, center anchor | fixed-position pop |
| `\fad(in_ms,out_ms)` | fade | line entry/exit |
| `\fscx<n>\fscy<n>` | scale X/Y (%) | big-active-word |
| `\blur<n>` | gaussian blur | neon glow |
| `\frz<deg>` | z-rotation | subtle tilt |
| `\1c&HBBGGRR&` | per-run primary color override | active-word recolor |
| `\b1` / `\bord<n>` / `\shad<n>` | bold / outline width / shadow depth | weight & depth |

**Karaoke mechanics (the part people get wrong):**
- `PrimaryColour` = color **after** (i.e. during/after) the highlight; `SecondaryColour` =
  color **before** it. So `Primary=yellow, Secondary=white` gives the classic white→yellow sweep.
- Each `\kN` value is **that word's own duration in centiseconds**, and the highlight offset
  **accumulates in order** from the Dialogue start — pass *durations*, not absolute offsets.
- ASS time resolution is **centiseconds** (`0:00:00.10`); SRT/VTT are milliseconds. Round once.
- There is **no per-word element**: for pops you emit **one Dialogue per word** (or a base line
  plus a layer-1 overlay per word), which is exactly what the reference project does.

Burn command (verified locally, `ass=` not `subtitles=`):

```bash
ffmpeg -buildconf | grep libass                 # must print --enable-libass
ffmpeg -i in.mp4 -vf "ass=captions.ass:fontsdir=./fonts" \
       -c:v libx264 -preset medium -crf 20 -c:a copy -movflags +faststart out.mp4
```

> Use the **`ass`** filter, not `subtitles`: `subtitles` runs the file through libavformat's
> converter and honors `force_style` overrides, which can **flatten karaoke timing**; `ass`
> hands the file straight to libass. (`ffmpeg -h filter=ass` on this box: `filename`,
> `original_size`, `fontsdir`, `alpha`, `shaping` — use `shaping=complex` if `auto` ever picks
> simple for your script.)

### 2.3 Python ASS libraries

| Lib | License | Stars | Notes |
|---|---|---|---|
| **pysubs2** | MIT | ~440★ | ✅ SSAStyle/SSAEvent + saves SRT/VTT/ASS from the same object; pure Python; verified v1.9.1 |
| pyass | MIT | small | lower-level ASS parsing/rendering, less community |
| PyonFX | **LGPL-3.0** | ~182★ | effect-authoring framework (anime typesetting); copyleft — avoid linking into proprietary code |
| python-ass / ass-imbk | MIT | small | parsing/serialization only |

### 2.4 Ready-made caption projects (copy patterns, not code wholesale)

| Project | License | Stars | What it does | Take-away |
|---|---|---|---|---|
| **[nicolaigaina/ai-video-captions](https://github.com/nicolaigaina/ai-video-captions)** | MIT | ~70★ | **Exactly our target architecture**: faster-whisper → **pysubs2 ASS generation** → FFmpeg burn; 6 styles (Hormozi, MrBeast, Karaoke, Minimal, Bounce, Classic); `caption-styles.config.json` shared by Next.js frontend **and** Flask backend; Docker | ✅ **port `backend/subtitles.py`** (per-word Dialogue duplication, char-based line grouping, `\kf` + `\t\fscx` pop, ASS `&HBBGGRR` colors, scale by `video_height/1920`) |
| **[Guhan-byte/Capite](https://github.com/Guhan-byte/Capite)** | MIT | ~12★ | 27 styles, word-level editing, MP4/SRT/VTT/ASS export, local | second style reference |
| **[francozanardi/tscaps](https://github.com/francozanardi/tscaps)** | engine **MIT** / studio **AGPL-3.0** / templates MIT | small | 49 CSS caption template folders (`template.json` + `style.scss`), word-level doc model, browser WebCodecs render, exports SRT/VTT/ASS/SBV/TXT | steal **template folder layout + export matrix**; don't take the AGPL studio app |
| **[nikhil-reddy05/auto-captions](https://github.com/nikhil-reddy05/auto-captions)** | MIT | small | Whisper per-word → ASS karaoke with pop-in, CLI flags for font/outline/pop, documents `fontsdir` + `shaping` + `PlayResX/Y=1080/1920` + `margin-v 120–180` for Shorts UI clearance | good CLI-flag checklist |
| **[chjm-ai/tiktok-karaoke-captions](https://github.com/chjm-ai/tiktok-karaoke-captions)** | (check) | small | mlx-whisper (Apple-silicon only) → SRT + karaoke ASS, auto-wrap headline, pill box | macOS-only ASR → skip, but its line-wrapping heuristics are useful |
| **[harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)** | MIT | **~120k★**, active | full short-video pipeline; subtitles via **moviepy `TextClip`**, word-by-word pop added v1.3.7 | ❌ wrong renderer for us (slow, no karaoke timing) — but proves demand |
| **ShortGPT** | MIT | ~8.1k★ | editor automation, subtitle pass | not a styling engine |
| xixihhhh/hotclip | (check) | small | pure-string ASS karaoke builder in TS | useful if the preview lives in TS |
| veedstudio/open-edit | Apache-2.0 | ~1.8k★ | browser editor; **renderer binary has a separate (non-OSS) license, macOS-only** | ❌ verify before use — likely not shippable |
| SubtitleEdit | MIT | ~14.8k★ | desktop subtitle editor, huge format coverage | good for manual QA of exports |

**Reference implementation worth porting (MIT):** `ai-video-captions/backend/subtitles.py` —
per-word ASS events with `\kf` karaoke **and** per-word `\t(0,120,\fscx120\fscy120)` pop,
char-count line grouping, style dataclass → `pysubs2.SSAStyle`, dimension scaling by
`video_height / 1920`.

### 2.5 Fonts (Latin + Indic) — all OFL, safe to bundle

| Script | Fonts (OFL) | Notes |
|---|---|---|
| Latin display | **Montserrat, Poppins, Anton, Archivo/Archivo Black, Inter, Bebas Neue** | the CapCut-ish caption look; Anton/Archivo Black for MrBeast-scale |
| Devanagari / Gujarati / Gurmukhi / Tamil + Latin | **Mukta** (EkType) | one family covers all four Indic scripts + Latin → ideal single bundle |
| Devanagari (Google) | **Noto Sans Devanagari** | safe fallback, huge coverage |
| Hindi display (Hind/Rajdhani/Teko, Google/ITF) | Rajdhani / Teko / Hind | condensed numeric/tech looks |

Shipping rule: put fonts in `./fonts`, pass `fontsdir=./fonts` to the `ass` filter, set
`fontFamily` in the preset and let **libass font fallback** (fontconfig) pick a covering font
for glyphs the primary family lacks — that is how mixed English+Hindi lines render correctly
without per-language style switching. Verify your build has fontconfig (`--enable-fontconfig`,
present here).

---

## 3. Rendering options comparison

| Approach | License | Animation ceiling | Speed (60 s, 1080×1920) | Offline | Indic shaping | Verdict |
|---|---|---|---|---|---|---|
| **ffmpeg + libass (`ass=` filter)** | ffmpeg (GPL build) + libass | **High** — karaoke, `\t` transforms, `\move`, `\fad`, `\blur`, per-word events | **~35 s** (M2, CRF20, published); **measured 2.0 s for a 6 s clip** on this box → ~35 s/60 s consistent | ✅ | ✅ HarfBuzz (verified) | ✅ **pick** |
| moviepy `TextClip` / Pillow compositing | MIT (moviepy) | Low–medium — static/pop text, **no karaoke timing model** | est. minutes (Python per-frame raster; MoneyPrinterTurbo path) | ✅ | ⚠️ Pillow needs libraqm for complex scripts | ❌ too slow, wrong abstraction |
| Remotion (React → video) | **custom license**: free ≤3 people, else $0.01/render ($100/mo) or $25/seat | Very high — anything CSS can do | render-bound, needs Chromium per render; ≥ realtime | ⚠️ | ✅ browser shaping | ❌ **not permissive OSS**, cost/risk for a product |
| Headless browser + WebCodecs/canvas (tscaps *studio* path) | **AGPL-3.0** (tscaps studio) or bespoke | Very high | fast in-browser | ✅ | ✅ | ❌ AGPL / lots of moving parts |
| ffmpeg `drawtext` | ffmpeg | Low — **no karaoke**, manual per-frame expressions | fast | ✅ | ❌ poor | ❌ explicitly cannot do karaoke |

Notes:
- Hard-burn **forces a video re-encode** — but you can (and should) burn **during** the main
  encode (single pass), so the marginal cost is libass rasterization only, which is small
  next to x264. NVENC (`-c:v h264_nvenc`) accelerates the encode side if you have a GPU.
- If users may want captions toggleable, additionally **mux** the same ASS/SRT as a soft
  subtitle stream (`-c:s mov_text`) — zero extra style work, since ASS is already authored.
- The only reason to leave libass: effects impossible in ASS (per-letter gradient fill,
  video-textured text, arbitrary masks). Those are ~5% of CapCut's caption catalog.

---

## 4. Portable preset format for 12 presets

### 4.1 Design principles

1. **One JSON file per preset**, design-time resolution fixed at **1080×1920** (the industry
   reference for shorts); runtime scales metrics by `target_h / 1920`.
2. **Colors as `#RRGGBB` hex** in JSON; the compiler converts to ASS `&HAABBGGRR`
   (BGR order, alpha `00` = opaque) so humans never touch BGR.
3. **Animation is a *strategy enum***, not free-form ASS — the compiler owns the 5 ASS
   generators (karaoke sweep, word pop, line slide, typewriter, static). New preset = new JSON,
   never new renderer code.
4. **Same JSON drives the browser preview** (CSS approximation) and the authoritative ffmpeg
   burn — the pattern already proven by `ai-video-captions/caption-styles.config.json`.
5. `version` + JSON-Schema validation on load; presets are immutable data shipped in the image.

### 4.2 Schema (full example — `presets/hormozi-pop.json`)

```json
{
  "$schema": "./preset.schema.json",
  "id": "hormozi-pop",
  "name": "Hormozi Pop",
  "version": 1,
  "refResolution": { "w": 1080, "h": 1920 },
  "typography": {
    "fontFamily": "Montserrat",
    "fontFallback": ["Noto Sans Devanagari", "Noto Sans"],
    "fontSize": 84, "bold": true, "uppercase": false, "letterSpacing": 0
  },
  "colors": {
    "base": "#FFFFFF", "highlight": "#FFD400",
    "outline": "#000000", "shadow": "#000000", "boxBackground": "#000000"
  },
  "layout": {
    "align": 2, "marginH": 60, "marginV": 420,
    "maxWordsPerLine": 4, "maxCharsPerLine": 20, "maxLineDurationMs": 2400,
    "pauseBreakMs": 350
  },
  "stroke": { "borderStyle": 1, "outlineWidth": 7, "shadowDepth": 3, "glowBlur": 0 },
  "animation": {
    "mode": "word_pop",
    "karaokeTag": "kf",
    "popScale": 1.18, "popInMs": 90, "popOutMs": 110,
    "slidePx": 60, "fadeInMs": 180, "fadeOutMs": 120
  }
}
```

| Field group | Purpose | Maps to ASS |
|---|---|---|
| `typography` | family + fallbacks, size at ref res | `Style.Fontname/Fontsize/Bold/Spacing` |
| `colors` | hex, human order | `PrimaryColour` (highlight) / `SecondaryColour` (base) / `OutlineColour` / `BackColour` |
| `layout` | anchoring, margins, grouping budgets | `Alignment/MarginL/R/V` + shared `group()` params |
| `stroke` | outline/shadow/box/glow | `BorderStyle/Outline/Shadow` + `\blur` |
| `animation` | strategy enum + timings | generator picks `\kf` / `\t` / `\move` / `\fad` |

### 4.3 Compiler (executed & verified against pysubs2 1.9.1)

```python
import pysubs2

def hex_to_rgb(h):
    h = h.lstrip("#"); return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

def alloc_durations(line):
    """Centiseconds per word; largest-remainder so sum(durs) == line span exactly."""
    span_cs = int(round((line[-1]["e"] - line[0]["s"]) * 100))
    raw = [(w["e"] - w["s"]) * 100 for w in line]
    base = [max(1, int(r)) for r in raw]                 # min 1cs so no word vanishes
    diff, i = span_cs - sum(base), 0
    order = sorted(range(len(line)), key=lambda i: raw[i] - int(raw[i]), reverse=True)
    while diff != 0 and order:
        idx = order[i % len(order)]
        base[idx] += 1 if diff > 0 else -1
        diff += -1 if diff > 0 else 1; i += 1
    return base

def build_ass(lines, preset, out_path):
    res = preset["refResolution"]; scale = res["h"] / 1920.0
    ss = pysubs2.SSAFile()
    ss.info["PlayResX"], ss.info["PlayResY"] = str(res["w"]), str(res["h"])

    t, c, o, L = preset["typography"], preset["colors"], preset["stroke"], preset["layout"]
    st = pysubs2.SSAStyle()
    st.fontname, st.fontsize = t["fontFamily"], round(t["fontSize"] * scale)
    st.primarycolor  = pysubs2.Color(*hex_to_rgb(c["highlight"]), 0)   # during highlight
    st.secondarycolor= pysubs2.Color(*hex_to_rgb(c["base"]), 0)        # before highlight
    st.outlinecolor  = pysubs2.Color(*hex_to_rgb(c["outline"]), 0)
    st.shadowcolor   = pysubs2.Color(*hex_to_rgb(c["shadow"]), 0)
    st.outline, st.shadow = round(o["outlineWidth"] * scale), round(o["shadowDepth"] * scale)
    st.borderstyle, st.bold = o["borderStyle"], t["bold"]
    st.alignment = L["align"]
    st.marginl = st.marginr = round(L["marginH"] * scale)
    st.marginv = round(L["marginV"] * scale)
    ss.styles["Default"] = st

    tag = preset["animation"]["karaokeTag"]                 # "kf" | "k" | "ko"
    for line in lines:                                      # strategy: karaoke_sweep
        parts = ["{\\%s%d}%s " % (tag, d, w["w"])
                 for w, d in zip(line, alloc_durations(line))]
        ss.events.append(pysubs2.SSAEvent(
            start=int(round(line[0]["s"] * 1000)),
            end=int(round(line[-1]["e"] * 1000)),
            text="".join(parts).rstrip()))
    ss.save(out_path, encoding="utf-8")
    return ss
```

Verified output (from the run — note exact `\kf` durations and matched SRT cues):

```
Dialogue: 0,0:00:00.10,0:00:01.12,Default,,0,0,0,,{\kf24}Let {\kf18}me {\kf38}show {\kf22}you
Dialogue: 0,0:00:01.30,0:00:02.34,Default,,0,0,0,,{\kf16}the {\kf52}fastest {\kf22}way {\kf14}to
...
1 / 00:00:00,100 --> 00:00:01,120 / Let me show you
```

The other two strategies were executed too:

```python
# word_pop — base line white, layer-1 overlay flashes+scales the active word
ss.events.append(SSAEvent(start=int(w["s"]*1000), end=int(w["e"]*1000), layer=1,
    text="{\\an2\\1c&H0000D4FF&\\t(0,90,\\fscx118\\fscy118)\\t(90,200,\\fscx100\\fscy100)}%s" % w["w"]))
# line_slide
text = "{\\fad(180,120)\\move(540,1460,540,1400)}%s" % " ".join(w["w"] for w in line)
```

Both files burned through ffmpeg without error.

### 4.4 The 12 shipped presets

| # | `id` | Look | Animation strategy (ASS mechanism) | Font | base → highlight |
|---|---|---|---|---|---|
| 1 | `hormozi-pop` | huge bold caps, active word flashes | **word_pop** — layer-1 per-word `\t` scale 118% | Montserrat ExtraBold | white → `#FFD400` |
| 2 | `mrbeast-bounce` | maximum size, thick black outline | **word_pop** with stronger bounce (`\t` 100→125→100) | Anton / Archivo Black | white → `#FFE600` |
| 3 | `tiktok-karaoke` | 3–4 words/line, left-to-right fill | **karaoke_sweep** `\kf` (verified render) | Montserrat SemiBold | white → `#FFD400` |
| 4 | `minimal-fade` | sentence-level, quiet | **static** + `\fad(400,300)` | Inter | `#F2F2F2` → same |
| 5 | `neon-glow` | dark bg, glowing edge | **static** + `\blur3` glow, `\bord10` | Poppins | `#7DF9FF` → `#FF4FD8` |
| 6 | `boxed-classic` | opaque caption bar (broadcast) | **static**, `BorderStyle=3`, tight line dur | Inter | white on `#000000` box |
| 7 | `slide-up` | line rises into place | **line_slide** `\move` + `\fad` | Montserrat | white → `#FFD400` |
| 8 | `typewriter` | characters appear one by one | **karaoke with `\ko`/transparent-secondary trick** (per-char `\k`) | Roboto Mono / DejaVu Sans Mono | transparent → white |
| 9 | `word-pop-soft` | gentle pop, rounded | **word_pop**, popScale 1.08, 160 ms | Poppins | white → `#4EF0FF` |
| 10 | `big-active-word` | active word grows, rest static | **word_pop** with `\fscx130` on current word only | Montserrat Bold | white → white (scale carries it) |
| 11 | `serif-editorial` | premium/quote look | **static** per phrase + `\fad` | Playfair Display / DejaVu Serif | `#FFFFFF` → `#E8C547` |
| 12 | `indic-karaoke` | Hindi/Indian-audience karaoke | **karaoke_sweep** + `shaping=complex`, font fallback to Indic | **Mukta** (+ Noto Sans Devanagari fallback) | white → `#FF9E00` |

Grouping knobs (`maxWordsPerLine`, `maxCharsPerLine`, `maxLineDurationMs`, `pauseBreakMs`) live
**in the preset**, because MrBeast-scale text needs 3 words/line while minimal styles can take
a full phrase.

### 4.5 Shipping mechanics

```
hyproai/
  presets/                     # 12 × *.json  (validated by preset.schema.json)
  fonts/                       # OFL woff/ttf bundled (Montserrat, Anton, Mukta, Noto Sans Devanagari…)
  captions/
    schema.py                  # dataclasses + validation
    grouping.py                # group() + alloc_durations()  (shared by ASS/SRT/VTT)
    ass_builder.py             # 5 strategy generators (§4.3)
    sidecars.py                # SRT/VTT/TXT/words.json writers
```

- **Preview parity:** frontend fetches `presets/*.json` and renders an approximate CSS preview
  (same colors/sizes/margins, `vh`-scaled); the ffmpeg burn is authoritative. This is the
  `ai-video-captions` shared-config pattern and avoids maintaining two style systems.
- **Resolution scaling:** generate ASS with `PlayResX/Y = target output size` (1080×1920,
  2160×3840 for 4K) and multiply every metric by `target_h/1920` — deterministic, no reliance
  on `original_size` heuristics.
- **Validation gate:** CI loads all 12 presets through the compiler and fails on schema errors
  or un-renderable tags; plus one golden-frame render test per preset (extract a frame, compare
  against a checked-in PNG with a perceptual diff).

---

## 5. Sidecar exports (SRT / VTT / TXT) — correctness rules

### 5.1 What each format can represent

| Format | Cue-level timing | Word-level timing | Styling/animation | Produced by |
|---|---|---|---|---|
| **ASS** | ✅ | ✅ (karaoke tags / per-word events) | ✅ full | `pysubs2` (same object) |
| **SRT** | ✅ | ❌ (no standard) | ❌ (players ignore `<b>/<i>/<font>` inconsistently) | `pysubs2.save("x.srt")` |
| **WebVTT** | ✅ | ⚠️ **inline `<hh:mm:ss.mmm>` timestamps** in cue text (word *start* only) | ⚠️ `<c>` classes, CSS | `pysubs2.save("x.vtt")` or hand-written |
| **TXT** | ❌ | ❌ | ❌ | plain join of words |
| **words.json** | ✅ | ✅ | n/a (source of truth) | your pipeline |

### 5.2 Single-source pipeline (verified)

```python
lines = group(words)                       # ONE grouping function (§4.3)
ass   = build_ass(lines, PRESET, "captions.ass")
ass.save("captions.srt", encoding="utf-8") # identical cue boundaries to the burn
ass.save("captions.vtt", encoding="utf-8")
open("captions.txt", "w").write(" ".join(w["w"] for w in words))
json.dump({"language": "en", "lines": lines}, open("captions.words.json", "w"))
```

Verified: 5 SRT cues with **exactly** the ASS Dialogue start/end (`00:00:00,100 --> 00:00:01,120`
vs `0:00:00.10,0:00:01.12`), and the VTT round-trips through pysubs2.

Optional **word-level VTT** (when a player/editor wants karaoke outside the burn):

```
WEBVTT

1
00:00:00.100 --> 00:00:01.120
Let <00:00:00.340>me <00:00:00.520>show <00:00:00.900>you
```

Text before the first tag belongs to the cue start; each tag is the **next word's start**;
last word needs no tag. (This is the shape stable-ts emits; players support it unevenly —
treat as progressive enhancement, never as the primary deliverable.)

### 5.3 Correctness rules / gotchas

1. **One `group()` for everything** — SRT cues, VTT cues and burned ASS lines must have
   identical boundaries, or users see the exported subtitle disagree with the video.
2. **Karaoke durations are per-word centiseconds that accumulate** — never write absolute
   offsets; assert `sum(durs) == round((line_end - line_start)*100)` (the largest-remainder
   allocator in §4.3 guarantees it; naive `int()` truncation drifts and leaves the last word
   un-highlighted).
3. **Never overlap cues** in SRT/VTT (players glitch); keep `cue[i].end <= cue[i+1].start`
   (ASS tolerates overlap, but keep the same discipline for parity).
4. **Round once:** ASS is centisecond, SRT/VTT millisecond. Convert from the float seconds
   directly in each writer; don't round-trip through an already-rounded string.
5. **`min 1cs` per word** so a 20 ms article ("a", "to") doesn't collapse to a zero-duration
   highlight that libass may skip.
6. **Indic:** group by ASR whitespace tokens (never split a token) — Devanagari matras are
   multi-codepoint; splitting breaks shaping. libass+HarfBuzz does the rest.
7. **whisperX drops unalignable tokens** (numbers, currency) — filter them out of `words`
   before grouping, or interpolate between neighbours, otherwise line spans and text disagree.
8. **TXT** is for transcripts/captions-on-social only: no timing, no styling.
9. `pysubs2` **drops karaoke/animation when saving SRT/VTT** — that is expected and correct:
   ASS carries the styling, sidecars carry the words and times.

---

## 6. License risk register

| Item | License | Decision |
|---|---|---|
| faster-whisper | MIT | ✅ ship |
| whisperX | BSD-2-Clause | ✅ ship |
| openai-whisper | MIT | ✅ optional |
| pysubs2 | MIT | ✅ ship |
| `srt`, `webvtt-py` | MIT / BSD-3 | ✅ if ever needed standalone |
| ffmpeg + libass + HarfBuzz | ffmpeg GPL build / libass ISC | ✅ runtime dependency (don't link ffmpeg code into your app; shell out) |
| ai-video-captions / Capite / auto-captions / MoneyPrinterTurbo | MIT | ✅ reference/port with attribution |
| tscaps engine + templates | MIT | ✅ (studio app is **AGPL-3.0** ❌) |
| **pysrt** | **GPL-3.0** | ❌ use pysubs2 instead |
| **aeneas** | **AGPL-3.0** | ❌ |
| **Meta MMS aligner weights** | **CC-BY-NC** | ❌ (non-commercial) |
| **Remotion** | custom: free ≤3 people, else $0.01/render ($100/mo min) or $25/seat | ❌ not permissive OSS |
| **stable-ts** | MIT | ⚠️ archived — reference only |
| **veedstudio/open-edit renderer** | separate non-OSS license, macOS-only | ❌ verify/skip |
| Fonts (Montserrat, Anton, Poppins, Mukta, Noto, Inter…) | **OFL** | ✅ bundle in `fonts/` |

---

## RECOMMENDED PICK

**Generation → style format → renderer → how the 12 presets ship:**

```
input.mp4
   │  ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 audio.wav        (optional, 1 line)
   ▼
faster-whisper  (MIT, word_timestamps=True, vad_filter=True)
   │   optional upgrade: whisperx.align() for frame-accurate timings   (BSD-2)
   ▼
words.json  ──────────────── THE SOURCE OF TRUTH ────────────────┐
   ▼                                                             ▼
group() → lines  ──►  ass_builder(preset JSON)  ──► captions.ass │
   │                          (pysubs2, MIT)                     │
   ├────────────────────►  captions.srt / captions.vtt / .txt  ◄──┘   (same boundaries)
   ▼
ffmpeg -i in.mp4 -vf "ass=captions.ass:fontsdir=./fonts" \
       -c:v libx264 -preset medium -crf 20 -c:a copy out.mp4
   (libass → HarfBuzz: karaoke + full Indic shaping; single pass with the encode)
```

**1. Generation — `faster-whisper` (MIT, 25.7k★) as default, `whisperX` (BSD-2, 24.4k★) as the
accuracy mode.** faster-whisper gives word timestamps 2–4× faster than anything else, offline,
pip-installable; whisperX's wav2vec2 alignment is the fix when pops land visibly late (fast
speech, accents) — accept its per-language model downloads and its "unalignable token" caveat.
Drop stable-ts (archived), aeneas (AGPL), MMS (NC).

**2. Style format — ASS v4+ authored by `pysubs2` (MIT) from JSON presets.** ASS is the only
permissively-licensed format with *native* word-timed karaoke (`\kf`), transforms (`\t`), motion
(`\move`), fades and glow — i.e. every CapCut-style effect — and it round-trips to SRT/VTT from
the same object. Presets are data (`presets/*.json`, schema-validated, versioned), never code.

**3. Renderer — ffmpeg's `ass=` filter → libass (HarfBuzz).** Verified on this machine: build
has `--enable-libass --enable-libharfbuzz`, `\kf` sweep renders correctly (frame checked),
6 s 1080×1920 clip burns in 2.0 s (~35 s per 60 s, matching published M2 numbers) — and the
burn folds into your existing encode pass. moviepy/Pillow is the wrong abstraction (no karaoke,
per-frame Python) and Remotion is off the table on license grounds (paid above 3 seats).

**4. Shipping the 12 presets** — `presets/{hormozi-pop, mrbeast-bounce, tiktok-karaoke,
minimal-fade, neon-glow, boxed-classic, slide-up, typewriter, word-pop-soft,
big-active-word, serif-editorial, indic-karaoke}.json, each declaring typography (with
`fontFallback` to Mukta/Noto Sans Devanagari), hex colors, layout/grouping budgets, stroke and
one of five `animation.mode` strategies (`karaoke_sweep | word_pop | line_slide | typewriter |
static`). One compiler (§4.3) turns any preset into ASS; the frontend reads the *same* JSON for
preview; CI compiles + golden-frame-renders all 12. Fonts ship OFL in `./fonts` and are passed
via `fontsdir`, with libass fallback covering Indic glyphs.

**5. Sidecars** — SRT + VTT + TXT + `words.json` are all derived from the *same* `group()` and
the same word array, so cue boundaries match the burned lines exactly (verified). Word-level
lives in `words.json` (canonical) and optionally as inline VTT timestamps; SRT stays cue-level
by specification. Karaoke centiseconds are allocated with largest-remainder rounding so
`sum(\kf) == line span` — no drift, no un-highlighted last word.

**Open questions before build:** (a) confirm target resolutions (1080×1920 only, or also 4K and
16:9 — the `scale = h/1920` rule must become per-axis for 16:9); (b) preview-parity tolerance
(CSS approximation vs a true libass-in-wasm preview); (c) GPU availability decides whether
whisperX alignment is on by default or an "accurate mode" toggle.
