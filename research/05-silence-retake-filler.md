# 05 — Silence Removal, Retake Removal, Filler Removal: Open-Source Research

**Scope:** best OSS building blocks for HyproAI's three flagship cutting features + the timeline/render primitives underneath them.
**Constraints applied:** local/offline preferred, pip-installable, permissive license, GPU optional, accuracy on Indian-accented English + Indic speech.
**Star counts / last-push dates verified via GitHub API on 2026-11-04.**

---

## 0. Executive summary (read this first)

| Feature | Direct mature OSS exists? | Verdict |
|---|---|---|
| Silence / pause cutting | **Yes** — `auto-editor` (Unlicense, 5.7k★, very active) is production-grade | Use as baseline + timeline exporter; but drive the *real* pipeline from VAD so filler/retake share the same word clock |
| Filler-word removal | **Partial** — several small MIT tools implement the full loop (`tightcut`, `timkulbaev/ai-video-editor`, `benpiper/auto-video-editor`, `CutScript`) | **Borrow their algorithms**, don't fork their apps |
| Retake / repetition removal | **No mature OSS.** ~6 tiny repos (1–14★) each implement a fragment | **Must build.** Components (ASR word clock + lexical/semantic near-dup + quality scoring) all exist under permissive licenses |
| Timeline/EDL primitives | **Yes** — ffmpeg `trim`/`concat`, `auto-editor --export`, OpenTimelineIO, MLT, moviepy | ffmpeg filter graph is the render path; `auto-editor --export premiere/resolve` is the review path |

**Headline:** the *detection* logic for all three features is small (~500 lines of Python total). The *hard* parts are (a) one shared word-accurate timeline, (b) not clipping consonants/breaths at cut points, (c) ASR quality on Indic + code-switched speech, (d) picking the *best* take rather than the *last* take.

---

## 1. Silence / pause cutting

### 1.1 auto-editor — **RECOMMENDED baseline**

| | |
|---|---|
| Repo | https://github.com/WyattBlue/auto-editor |
| License | **Unlicense** (public domain, fully permissive — no attribution obligation) |
| Stars / maintenance | **5,758★**, last push **2026-11-02** — actively maintained |
| Approach | Audio-loudness analysis → integer labels per moment (`0`=silent/cut, `1`=active/keep, up to 255 classes) → render, or **export a timeline to an NLE** |
| Install | `pip install auto-editor` (also Homebrew; ships ffmpeg handling internally) |

```bash
# cut dead air, keep 0.2s breathing room around every kept span (default)
auto-editor in.mp4 --margin 0.2s

# asymmetric breathing room: 0.15s before, 0.4s after each kept span
auto-editor in.mp4 --margin 0.15s,0.4s

# DON'T render — export an editable cut list for Premiere / Resolve / FCP / Kdenlive / Shotcut
auto-editor in.mp4 --export premiere
auto-editor in.mp4 --export clip-sequence      # one file per kept segment

# preview what would be cut, tune the loudness threshold
auto-editor in.mp4 --preview
auto-editor in.mp4 --edit audio:threshold=-38dB --margin 0.2s
```

**Why it fits us**
- `--margin` *is* the "smart merge threshold" concept: padding is added around kept spans, so gaps shorter than ~2×margin collapse naturally while real pauses survive.
- Multi-class labels (`--edit:N` / `--when:N`) let us later add our own label source (e.g. "VAD says speech" = label 2) — it is designed for pluggable detectors.
- **`--export premiere|resolve|final-cut-pro|shotcut|kdenlive|clip-sequence`** is exactly the EDL primitive we need: run detection, hand the user a reviewable cut list, render only on approval.
- `--when-active cut --when-inactive nil` inverts the output so you get *only* the dead air — useful for QA/preview UI.

**Cons**
- **Purely loudness-based.** Cuts music beds, breaths, and quiet-but-intentional moments; keeps keyboard clicks and room tone spikes. No semantic awareness.
- No transcript → cannot do filler/retake in the same pass.
- `--margin` is padding, not a "gap ≥ X ⇒ cut" rule; long natural pauses (2s+) still get cut unless you raise the threshold. Needs tuning per creator.
- Rendering path is re-encode based (fast enough, but not stream-copy).

**Fit for our pipeline:** use auto-editor as (a) the *reference implementation* whose behaviour we must beat, (b) the *timeline exporter* for NLE hand-off, and (c) an optional "fast path" mode. Do **not** build the product's core cut engine on loudness alone.

---

### 1.2 jumpcutter — legacy reference

| | |
|---|---|
| Repo | https://github.com/carykh/jumpcutter |
| License | **MIT** |
| Stars / maintenance | **3,144★**, last push **2024-02-28** — effectively unmaintained (README directs you to a fork) |
| Approach | Per-frame audio loudness (RMS) classification with `frame_margin`; can *speed up* silent sections instead of cutting |
| Install | clone + `pip install -r requirements.txt` (not on PyPI) |

```bash
python jumpcutter.py --input_file in.mkv --sounded_speed 1 \
  --silent_speed 999999 --frame_margin 2 --frame_rate 30
```

**Pros:** MIT, famous, `silent_speed` (speed-up rather than cut) is a genuinely useful mode HyproAI should copy.
**Cons:** dumps **every frame as an image file** to a temp dir (huge disk, slow, breaks on long videos); open issues about **A/V desync and video not being cut at all** (#217); unmaintained; not pip-installable.
**Verdict:** read for ideas only. Do not depend on it.

---

### 1.3 FFmpeg `silencedetect` / `silenceremove`

| | |
|---|---|
| Repo | https://github.com/FFmpeg/FFmpeg (filters docs) |
| License | **LGPL-2.1+/GPL-2+** depending on build (system `ffmpeg` binary is fine; we just shell out) |
| Maintenance | Canonical, forever |
| Approach | `silencedetect` = *measurement only*, prints `silence_start/silence_end` to stderr; `silenceremove` = *audio-only* trimming |

```bash
# 1) detect — logs timestamps, edits nothing
ffmpeg -hide_banner -i in.mp4 -vn -af silencedetect=noise=-35dB:d=0.45 -f null - 2>&1 \
  | grep -E 'silence_(start|end)'

# 2) invert to keep-ranges, then cut + rejoin in ONE pass (video+audio stay locked)
#    keep[i] = [prev_silence_end, this_silence_start]  (± margin)
ffmpeg -i in.mp4 -filter_complex "
  [0:v]trim=start=0.4:end=3.10,setpts=PTS-STARTPTS[v0];
  [0:a]atrim=start=0.4:end=3.10,asetpts=PTS-STARTPTS[a0];
  ... (repeat per keep-range) ...
  [v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]" -map "[v]" -map "[a]" out.mp4
```

**Pros:** zero Python deps, frame-accurate when re-encoding, total control over margins, works identically on Indic audio (pure signal processing).
**Cons:** `silenceremove` **cannot** cut video (audio-only filter) — a very common trap; hand-rolling the invert-and-concat step is where bugs live (A/V drift, PTS, overlapping ranges, keyframe issues); no merge/padding semantics out of the box.
**Verdict:** this is the *rendering primitive*, not the *detector*. Pair it with a real VAD.

---

### 1.4 VAD / ASR-driven detectors (the ones that matter)

| Name | Repo | License | Stars / last push | Approach | Install |
|---|---|---|---|---|---|
| **Silero VAD** | snakers4/silero-vad | **MIT** | **10,591★** / 2026-10-22 | ~2 MB neural VAD, ONNX+PyTorch, 8/16 kHz, streaming + batch, **language-agnostic** (works on Indic out of the box) | `pip install silero-vad` |
| **faster-whisper** | SYSTRAN/faster-whisper | **MIT** | **26,461★** / 2026-11-02 | CTranslate2 Whisper, **built-in Silero VAD** (`vad_filter=True`), `word_timestamps=True`, ~4× faster than openai-whisper | `pip install faster-whisper` |
| **WhisperX** | m-bain/whisperX | **BSD-2-Clause** | **25,080★** / 2026-10-22 | faster-whisper + **wav2vec2 forced alignment** → sub-100 ms word timestamps; optional pyannote diarization | `pip install whisperx` |
| WebRTC VAD | agnation/py-webrtcvad (forks) | BSD-3-Clause | small / stale | GMM energy VAD, negligible CPU | `pip install webrtcvad` |
| pyannote segmentation 3.0 | pyannote/pyannote-audio | MIT but **gated** (HF token + accept conditions) | active | SOTA diarization/VAD, GPU-ish, heavy | `pip install pyannote.audio` |

```python
# Silero — the detector we should standardize on
import torch, numpy as np
from silero_vad import load_silero_vad, get_speech_timestamps, read_audio
model = load_silero_vad()                      # torch hub / onnx, ~2MB
wav = read_audio("in.wav", sampling_rate=16000)
ts = get_speech_timestamps(wav, model, sampling_rate=16000,
                           min_silence_duration_ms=500,   # dead-air floor
                           speech_pad_ms=300)             # context padding
# -> [{'start': samps, 'end': samps}, ...]  (return_seconds=True for float seconds)
```

```python
# faster-whisper — one call gives VAD spans AND word clock
from faster_whisper import WhisperModel
m = WhisperModel("large-v3-turbo", device="auto", compute_type="int8")
segs, info = m.transcribe("in.wav", vad_filter=True,
                          vad_parameters=dict(min_silence_duration_ms=500,
                                             speech_pad_ms=300),
                          word_timestamps=True,
                          language="en",
                          initial_prompt="uh um like wait, verbatim: ",   # keep disfluencies!
                          condition_on_previous_text=False)
words = [(w.word, w.start, w.end, w.probability) for s in segs for w in s.words]
```

**Third-party benchmark note:** Picovoice's 2026 VAD comparison measured Silero at **RTF 0.00429** (≈15 s of CPU per *hour* of audio) — VAD is never the bottleneck. WebRTC VAD is faster but far less accurate; pyannote is more accurate on overlapping speech but ~200× slower on CPU.

**Critical gotcha — Whisper strips disfluencies.** Whisper is trained to normalise away "uh/um", so a vanilla transcript **cannot** drive filler removal. `tightcut` documents the fix: `initial_prompt` with verbatim filler tokens + `condition_on_previous_text=False`. For Indic/Hinglish, consider AI4Bharat's **IndicConformer** (`ai4bharat/indic-conformer-600m-multilingual`, **MIT**, all 22 official Indian languages) as an alternate ASR that does not normalise the same way.

**Word-timestamp accuracy caveat:** Whisper's own `word_timestamps=True` drifts (known issues SYSTRAN/faster-whisper#120/#125); WhisperX's forced alignment is materially better (paper reports 93.2% vs 85.4% word-boundary precision at a 200 ms collar) **but** wav2vec2 alignment degrades on unfamiliar accents/domain vocab. **Mitigation we should adopt regardless:** after alignment, snap each word boundary to the nearest local RMS/energy minimum within ±80 ms. That single step removes most "cut mid-consonant" artifacts.

---

### 1.5 Transcript-gap / whole-app cutters (silence *and* fillers together)

| Name | Repo | License | Stars | Notes |
|---|---|---|---|---|
| **tightcut** | AndreaGiulianini/tightcut | **MIT** | 7★ / 2026-05 | Single Python script: faster-whisper word timestamps → long pauses + configurable filler list → ffmpeg re-encode. **Best minimal reference implementation.** Has `.whisper.json` + `.keyframes.json` caches so you can retune thresholds without re-transcribing. Italian-tuned by default. |
| **timkulbaev/ai-video-editor** | same | **MIT** | 14★ / 2026-02 | **Full pipeline we should study hardest**: Silero VAD → Whisper large-v3 → *edit_decisions* → frame-accurate ffmpeg segment extraction → concat → encode. YAML-configurable thresholds. Also has restart/repeat/filler logic (see §2). |
| **benpiper/auto-video-editor** | same | **MIT** | 12★ / 2026-09 | Amplitude silence + Whisper filler list + freeze-frame detection + **audio crossfade** (`--no-crossfade` to disable) + optional RVM background removal. REST API + web UI. |
| **CutScript** | DataAnts-AI/CutScript | **MIT** | 270★ / 2026-03 | Descript-like: WhisperX word-level transcript, delete word ⇒ cut video, **AI filler removal via LLM (Ollama/OpenAI/Claude)**, ffmpeg stream-copy *and* re-encode export, DeepFilterNet "Studio Sound". |
| **FireRed-OpenStoryline** | FireRedTeam/FireRed-OpenStoryline | **Apache-2.0** | **3,628★** / 2026-07 | Agentic editor with an **"ASR-based rough cut skill … removal of filler words, disfluencies, and repeated sentences"** (2026-03-22). Node `speech_rough_cut.py` is a clean, reusable design: LLM per-sentence keep/drop → group by 400 ms gap → ffmpeg cut → **remap ASR timestamps after deletions**. |
| **rescript** | wassgha/rescript | ⚠️ **PolyForm Noncommercial 1.0** | 1,028★ / 2026-10 | Technically excellent (one-click "Remove fillers" + "Remove silences ≥0.3 s", ffmpeg filter-graph export, EDL/AAF export) — **but the license forbids commercial use. Reference only, never vendored.** |
| **Video-Silence-Remover** | lucaslattari/Video-Silence-Remover | ⚠️ **no license** | 45★ / 2023 | pydub + moviepy. Unlicensed → not usable. |
| **Concat** | jub0t/Concat | ⚠️ **AGPL-3.0** | **14,037★** | CapCut replacement, scriptable JSON-RPC + MCP. AGPL is viral for our OSS product — reference only. |

**tightcut usage (the pattern to copy):**
```bash
pip install faster-whisper
python tightcut.py in.mp4 --model large-v3-turbo \
  --max-silence 0.6 --aggressive --pad 0.15 \
  --fillers "ehm,uhm,cioè,um,uh,like,wait" --cache
```

---

### 1.6 Smart-merge thresholds — what the field actually uses

| Parameter | Typical values | Source |
|---|---|---|
| Breathing room / margin | **0.2 s default**, 0.1–0.5 s | auto-editor `--margin` |
| Silence floor (cut only gaps ≥ this) | **0.3–1.0 s**; Descript's "word gap" default 1.0 s, users shorten to **0.2 s** | Descript, tightcut, rescript (≥0.3 s) |
| VAD `min_silence_duration_ms` | 400–600 ms | Silero/faster-whisper defaults |
| VAD `speech_pad_ms` | 200–300 ms | preserves word onsets |
| Sentence-split pause | 0.8–1.0 s | Tribe-Video-Cleaner uses 1.0 s; FireRed groups at 400 ms |
| Short-burst / false-start floor | drop isolated VAD spans < **0.3–2.0 s** | timkulbaev `min_filler_duration_sec=0.3`, `max_burst_duration_sec=2.0` |
| Post-cut audio micro-fade | **10–30 ms** equal-power | avoids clicks from off-zero-crossing cuts |

**Asymmetry that matters:** pad **more after** than before (e.g. 0.15 s before / 0.35–0.4 s after). Speakers inhale *before* the next word; cutting the inhale is what makes jump cuts sound "gasp"-y.

**Descript's "word gap ≠ silence" caveat (worth stealing as a warning in our UX):** Descript removes *word gaps* (any non-speech region between recognised words), so waveform energy without a recognised word also gets deleted — users complain it eats breaths and room tone. Our VAD-based approach avoids this class of bug by construction.

---

## 2. Retake / repetition ("flub") removal

### 2.1 Reference behaviour from proprietary tools (defines required behaviour)

| Product | Behaviour to replicate |
|---|---|
| **Descript "Remove Retakes"** (paid) | One click; groups repeated takes; **"Avoid harsh cuts"** option that *analyses surrounding audio and skips any removal that would clip into a nearby word or leave an awkward cut* — i.e. a per-candidate safety check, not blind deletion. Also offers *Delete* / *Delete and replace with gap* (preserve timing) / *Ignore* / *Remove from transcript only* per item. |
| **AutoCut Repeat** (Premiere + Resolve plugin, paid) | Detects repeated takes in **80+ languages**, optional **reference script** for better matching, dedicated review UI where you accept/reject each repetition before it hits the timeline. |
| **TimeBolt "Remove Bad Takes"** | Word-level transcript → **"False Start Detection"** + **"Retake Detection"** with a user-set **match tolerance** ("Look Ahead Lines") → **automatically selects the *last* cut as the best take** → claims 90–98% cut automation on scripted footage. |
| **Gling** | Cuts silence + filler words + **bad takes** in one pass, aimed at YouTubers. |
| **Claude + Premiere MCP workflow** (public write-up) | Transcribe (ElevenLabs Scribe) → Claude groups repeated segments → keeps the final clean one → applies as **soft markers** so the human reviews before anything is destroyed. **This "markers first, cut second" pattern is the right UX for us.** |

**Documented failure modes to design against** (EditBuddy, "Why AI Retake Removal Cuts the Wrong Take"):
1. It keeps the **last** take, not the **best** take.
2. A **single stumbled word** inside a unique sentence is not a "repeat" → belongs to the filler/flub stage, not the retake stage.
3. **Deliberate repetition** (chorus, refrain, call-back) gets destroyed.
4. **Tight trimming clips the breath** at the boundary.
5. **Paraphrased** retakes ("same point, different words") never match literal dedup.
6. **Bad transcripts** (strong accent, crosstalk, music bed, low mic) make the detector blind — *directly relevant to Indian-accented English and Hinglish code-switching*.
7. Technically correct but **rhythmically choppy** result.

### 2.2 What OSS actually exists (all fragments, none a product)

| Name | Repo | License | Stars | Approach | Verdict |
|---|---|---|---|---|---|
| **timkulbaev/ai-video-editor** | https://github.com/timkulbaev/ai-video-editor | **MIT** | 14★ / 2026-02 | **`_remove_restart_phrases()`**: (a) explicit trigger phrases ("cut cut", "кат кат") → drop the *whole* VAD speech segment containing them; (b) **repeated sentence starts** — key = first 3 normalised words of each transcript segment; if two *consecutive* segments share the key, drop the earlier one. Plus short-burst removal (<2 s) before merging. | **Closest OSS to our feature.** Algorithm is naive (3-word prefix only, adjacent only) — correct baseline, needs generalising |
| **grafup/Tribe-Video-Cleaner** | https://github.com/grafup/Tribe-Video-Cleaner | ⚠️ **no license** | 13★ / 2026-04 | **`detectDoubleTakes()`** — the best *algorithm* found: split words into sentences on ≥1.0 s pauses; compare **adjacent** sentences; require word-count ratio ≥0.5 and ≥3 words each; similarity = **(Jaccard + normalised Levenshtein)/2** ≥ sensitivity; group; **score each take = mean(word confidence) × word count**; mark `keep` on max score, `remove` on the rest. | **Copy the algorithm verbatim** (unlicensed repo → re-implement, don't copy code) |
| **ronaldtsecf/reel-auto-cut** | https://github.com/ronaldtsecf/reel-auto-cut | **MIT** | 7★ / 2026-08 | Cantonese IG-reel kit: whisper + **Gemini two-stage retake detection**; plus a **deterministic, zero-API `repeat_scan.py`** — global **5-gram** re-occurrence + near-distance (<15 chars) **4-gram** scan over the normalised transcript, with an allow-list for intentional refrains, exit-code gated for CI. Renders **EDL**. | **Steal `repeat_scan.py` as a QC gate** — cheap, language-agnostic, catches residual duplicates after cutting |
| **FireRed-OpenStoryline** `speech_rough_cut` | https://github.com/FireRedTeam/FireRed-OpenStoryline | **Apache-2.0** | **3,628★** / 2026-07 | LLM is shown each ASR sentence with prev/next context + history of prior decisions → returns keep/drop JSON → group survivors by 400 ms gap → ffmpeg-cut each range → **`calibrate_asr_times()` remaps all timestamps by prefix-sum of deleted durations**. | **Steal the timestamp-remapping utility and the LLM-fallback architecture** |
| verabredung2021-cpu/kocut | https://github.com/verabredung2021-cpu/kocut | ⚠️ no license | 2★ / 2026-06 | Korean auto-cut: subtitles + silence/filler/**retake** detection + **SRT/EDL export** | Unlicensed; note only |
| leonardoborgesdev/retake-app | https://github.com/leonardoborgesdev/retake-app | **MIT** | 1★ / 2026-08 | iOS app: on-device silence/**retake** detection | Trivial |
| EverythingAI-Pro/ai-video-editor | https://github.com/EverythingAI-Pro/ai-video-editor | NOASSERTION | 25★ | "auto-cut filler … edit by conversation" | Unclear license |

**Conclusion: there is no mature open-source retake remover.** The largest real-world implementations are all proprietary (Descript, AutoCut, TimeBolt, Gling). We must build it — but every component is available under MIT/Apache/Unlicense.

### 2.3 Component stack for the build

| Component | Pick | License | Notes |
|---|---|---|---|
| ASR + word clock | **faster-whisper** `large-v3-turbo` (`int8` CPU / `float16` GPU) | MIT | `vad_filter=True`, `word_timestamps=True`, verbatim `initial_prompt`, `condition_on_previous_text=False` |
| Word-boundary precision | **WhisperX align** (wav2vec2) + energy-minimum snap | BSD-2 | only if cuts look off; adds a second pass |
| Indic / native-Indic ASR | **AI4Bharat IndicConformer** (22 languages) or IndicWhisper | MIT | for non-English Indian languages; Hinglish stays on Whisper |
| Cheap lexical near-dup | token Jaccard + normalised Levenshtein + n-gram repeat | stdlib / `python-Levenshtein` | gate that removes 95% of pairs before embedding |
| Semantic near-dup (paraphrase recall) | **`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`** (384-d, 50+ languages incl. Hindi/Bengali/Tamil/Telugu) | **Apache-2.0** (`sentence-transformers` PyPI) | CPU-friendly, ~120 MB. Upgrade path: `paraphrase-multilingual-mpnet-base-v2` (768-d) or `BAAI/bge-m3` (Apache-2.0, 100+ langs) |
| Clustering | greedy adjacent-pair grouping (Tribe) → optional agglomerative on cosine ≥0.85 within a ±60 s window | scikit-learn | |
| Audio quality score | **DNSMOS P.835 ONNX** via `speechmos` PyPI / `TigreGotico/dnsmos-onnx` | **MIT** (model weights) | no-reference SIG/BAK/OVRL. **Avoid PESQ** — the reference implementation's license forbids normal commercial use |
| Cheap loudness score | `ffmpeg loudnorm` (integrated LUFS) + true peak | — | catches clipped/mic-too-far takes |
| ASR confidence | mean word `probability` from faster-whisper; mean segment `avg_logprob` | free | Tribe's `mean_conf × word_count` is a good default |
| Reference-script match | **`jiwer`** WER against the creator's script | MIT | only when a script exists (AutoCut's "add your script" feature) |
| LLM tie-breaker (optional, offline via Ollama) | any local 7–8B instruct model | varies | FireRed's per-sentence keep/drop pattern; **only for contested clusters**, never wholesale |
| Post-cut QC gate | reel-auto-cut-style 5/4-gram `repeat_scan` | MIT | exit non-zero if residual duplicates remain |

---

## 3. Filler-word removal

### 3.1 Direct implementations

| Name | Repo | License | Stars | Approach |
|---|---|---|---|---|
| **tightcut** | AndreaGiulianini/tightcut | **MIT** | 7★ | faster-whisper word timestamps → configurable filler list + long pauses → cut → re-encode. Cacheable thresholds. |
| **timkulbaev/ai-video-editor** | same | **MIT** | 14★ | `words.en` + `words.ru` lists; drops filler ranges ≥0.3 s; **splits** the containing keep-segment around the filler (does not drop the whole segment); padding applied *last* so breaths survive. |
| **benpiper/auto-video-editor** | same | **MIT** | 12★ | Whisper → filler list (`--fillers "um;uh;like"`) → silence amplitude → freeze-frame → crossfaded ffmpeg concat. |
| **CutScript** | DataAnts-AI/CutScript | **MIT** | 270★ | **LLM-driven filler detection** (`POST /ai/filler-removal`, works with local Ollama) on top of WhisperX word spans. |
| **Tribe-Video-Cleaner** | grafup/Tribe-Video-Cleaner | ⚠️ no license | 13★ | Windowed matcher over multi-word fillers, sorted longest-first so sub-phrases don't shadow the full phrase. |
| **FireRed-OpenStoryline** | FireRedTeam/FireRed-OpenStoryline | **Apache-2.0** | 3,628★ | LLM classifies each ASR sentence (filler / disfluency / repeated / keep). |
| **words/fillers** | https://github.com/words/fillers | **MIT** | 48★ | Just a curated **list of English filler words** — good seed lexicon. |
| **ezxzeng/um_detector** | https://github.com/ezxzeng/um_detector | ⚠️ none | 42★ | Old (2019) filler detector. |
| **SilenceTrimmer** | EfficientTools/SilenceTrimmer | **MIT** | 4★ | macOS SwiftUI + local Whisper shell script. |
| **WhisperChain** | chrischoy/WhisperChain | **BSD-3** | 332★ | "AI will clean up your transcript" — general cleanup, not a cutter. |
| rescript | wassgha/rescript | ⚠️ **PolyForm Noncommercial** | 1,028★ | One-click "Remove fillers" + silence ≥0.3 s. **Non-commercial → reference only.** |

### 3.2 Rules that make filler removal actually work

1. **ASR must not normalise disfluencies away.** Use `initial_prompt` seeded with the filler tokens + `condition_on_previous_text=False` (tightcut's documented trick). Otherwise the transcript simply has no "um" to delete.
2. **Minimum filler duration ≥0.3 s** — shorter cuts produce jarring micro-gaps (timkulbaev).
3. **Split, don't drop.** A filler inside a sentence should split the keep-segment in two, not delete the sentence (timkulbaev's `_remove_fillers`).
4. **Apply breathing-room padding *after* filler removal**, then re-merge overlapping segments, or you double-pad.
5. **Word-boundary safety check (Descript's "Avoid harsh cuts"):** skip any filler whose start is within ~80 ms of a neighbouring word, or whose removal would leave <100 ms of context — report it as "needs review" instead of auto-cutting.
6. **Multi-word fillers** ("you know", "I mean", "so basically") must be matched longest-first.
7. **Locale lexicon.** For Indian creators the list is not just `um/uh/like`:
   - English: `uh, um, er, ah, like, you know, i mean, so, actually, basically, right, okay, well, wait, hmm, mhm`
   - Hinglish/code-switch: `matlab, acha/okay, toh, yaar, na, naa, matlab kya, see, basically yaar`
   - Indic transliterations vary wildly → prefer **LLM-assisted detection** (CutScript pattern) for the long tail, with a deterministic list as the fast path.
8. **Single flubs ≠ retakes.** A stumbled word inside a unique sentence must be handled here, not by the retake pass (EditBuddy's rule).

---

## 4. Timeline / EDL / cutting & reassembly primitives

| Primitive | Repo | License | Stars | Use it for |
|---|---|---|---|---|
| **ffmpeg `trim`/`atrim` + `concat` filter** | FFmpeg/FFmpeg | LGPL/GPL (shell out) | — | **The render path.** Frame-accurate, A/V locked, supports `acrossfade` at joins. One pass, N inputs. |
| **ffmpeg concat *demuxer*** | FFmpeg/FFmpeg | LGPL/GPL | — | Fast **stream-copy** join when all segments already share codec/params — but cuts land on **keyframes**, so only use for previews or when re-encoding anyway. |
| **auto-editor `--export`** | WyattBlue/auto-editor | Unlicense | 5,758★ | `premiere` / `resolve` / `final-cut-pro` / `shotcut` / `kdenlive` / `clip-sequence`. **Cheapest way to give users a reviewable cut list.** |
| **OpenTimelineIO** | AcademySoftwareFoundation/OpenTimelineIO | **Apache-2.0** | **2,055★** / 2026-10-31 | Industry timeline interchange (→ Premiere/Resolve/FCP). Use if we ever need multi-track, not just a cut list. |
| **pycmx** | iluvcapra/pycmx | **MIT** | 31★ | CMX3600 **EDL parser** (parser, not writer — we'd emit EDL text ourselves; it's a trivial format). |
| **moviepy** | Zulko/moviepy | **MIT** | **15,107★** / 2026-08 | Pythonic `subclips()` + `concatenate_videoclips()`. **Slow** (frame-by-frame, re-encodes per clip, PIL in the loop) — fine for prototypes, not for 5-minute renders × N users. |
| **MLT / MLT Framework** | mltframework/mlt | **LGPL-2.1** | 1,870★ / 2026-10-29 | Full NLE engine (powers Kdenlive). Overkill; LGPL linking obligations if embedded. |
| **pydub** | jiaaro/pydub | MIT | — | Convenient audio-only slicing; not frame-accurate video. |
| **`xfade` / `acrossfade`** | FFmpeg | LGPL/GPL | — | Video dissolve + audio crossfade. **For dialogue, prefer a hard cut + 10–30 ms equal-power micro-fade** — long crossfades on spoken lines read as mistakes (pro audio consensus). |
| montage-ai | mfahsold/montage-ai | NOASSERTION | 63★ | transcript editing + **OTIO/EDL export** — architecture reference. |
| Gitruck CLI | Gitruck/cli | **MIT** | **962★** / 2026-10-30 | "One command turns talking-head raw footage into a re-editable project" (Chinese). Proof the market wants this; useful reference for project-file design. |

**Render strategy recommendation:** detect → `keep_ranges` (sorted, non-overlapping, margin-expanded) → **single `filter_complex` re-encode** (`trim/atrim/setpts/asetpts` per range + `concat=n=K:v=1:a=1`, `libx264 -crf 18 -preset veryfast`, `aac 192k`), with a 10–30 ms `afade` at each join. Offer `--export` EDL/Premiere XML so heavy renders happen in the user's NLE instead of ours.

---

## 5. Accuracy notes specific to Indian-accented English + Indic speech

1. **Silence/VAD detection is language-agnostic** — Silero's energy+context model works on Tamil/Telugu/Hindi audio with no tuning. This is the *safest* of our three features on Indic input.
2. **ASR is the risk surface.** Whisper large-v3 is strong on Indian English but degrades on heavy accent + code-switching; word *timestamps* degrade further. Since filler + retake both depend on the word clock, **timestamp quality, not WER, is our real dependency.**
3. **Mitigations:** (a) force `language=` when known; (b) verbatim `initial_prompt` including the creator's domain vocabulary and script; (c) WhisperX/`wav2vec2` forced alignment, then **energy-minimum snap**; (d) fallback ASR: **AI4Bharat IndicConformer** (MIT, 22 Indian languages) for native-Indic, IndicWhisper for transliterated mixes.
4. **Code-switching (Hinglish)** breaks both the filler lexicon and the near-dup tokenizer — normalise by lowercasing, stripping punctuation, **and optionally transliterating to a common script** before computing Jaccard/n-grams. Sentence-transformers multilingual models handle cross-script similarity natively, which is why the semantic stage matters more here than in a pure-English product.
5. **Paraphrase recall** (same point, different words) is only obtainable from the embedding stage — lexical matching alone will miss it.
6. **Never auto-destroy.** Follow the Claude+Premiere pattern: emit **soft markers / a review list first**, cut on approval. Descript and AutoCut both ship a review UI for exactly this reason.

---

## 6. RECOMMENDED PICK

### 6.0 One-sentence answer

**Build our own single "cutlist engine"** driven by **Silero VAD + faster-whisper word timestamps**, with `auto-editor` (Unlicense) as the baseline we must beat and as our NLE exporter; **retake removal must be built** from a lexical→semantic near-dup stack + a DNSMOS/confidence/length quality scorer, with `repeat_scan`-style QC afterwards; **filler removal** is a word-range subtraction on the same clock. Render with one ffmpeg `filter_complex` pass (hard cut + 10–30 ms micro-fade), export EDL/Premiere XML for review.

---

### 6.1 SILENCE REMOVAL — concrete algorithm

**Detector (not auto-editor's loudness, but VAD + optional loudness cross-check):**

```
INPUT: video
 1. ffmpeg -vn → 16 kHz mono WAV
 2. Silero VAD v6 (threshold 0.5, min_silence_duration_ms=500, speech_pad_ms=300)
    → raw speech spans S = [(s1,e1), …]
 3. SHORT-BURST FILTER (pre-merge): drop isolated spans < 0.30 s
    (coughs / false starts). Config: burst_floor = 0.30 s   [timkulbaev]
 4. SMART MERGE: merge spans with gap < min_gap
       min_gap = 0.80 s   (default; expose 0.3–1.5 s as "Pacing" slider)
    → merged spans M = [(S1,E1), …]        [timkulbaev _merge_close_segments]
 5. MARGIN EXPAND (asymmetric breathing room):
       start' = max(0,   start - 0.15 s)
       end'   = min(T,   end   + 0.35 s)
    then re-merge any spans that now overlap   [auto-editor --margin 0.15s,0.35s]
 6. ENERGY SNAP (post-step, our own):
       for each boundary, search ±80 ms for the local RMS minimum → snap.
       Prevents clipping consonants / inhales.   [WhisperX accent caveat fix]
 7. keep_ranges = sorted, non-overlapping spans.
    Cross-check (optional, QA mode): ffmpeg silencedetect(noise=-35dB, d=0.45)
    → flag any keep_range that is >70% sub-threshold as "suspect" for review.
 8. EXPORT:
      a) render  — one ffmpeg pass (§4) with 15 ms equal-power afade at each join
      b) review  — auto-editor --export premiere|resolve  OR our own CMX3600 EDL
```

**Acceptance targets:** no cut mid-word (energy-snap validation); ≥0.15 s pre / ≥0.35 s post retained; gaps <0.80 s never cut; runtime ≤ 0.05× realtime on CPU for detection (Silero RTF ≈0.004 + whisper large-v3-turbo ≈0.03–0.05×).

**If we only need silence shipped tomorrow:** `auto-editor in.mp4 --margin 0.15s,0.35s --export premiere` — Unlicense, 5.7k★, actively maintained, exports a reviewable timeline. Ship that as v0, then swap in the VAD cutlist in v1 (which we need anyway for filler + retake).

**Do not use:** jumpcutter (unmaintained, frame-dumping, A/V desync bugs), `silenceremove` (audio-only), moviepy (too slow for production).

---

### 6.2 RETAKE REMOVAL — concrete algorithm (no direct OSS ⇒ what to build)

**Module: `retakes.py` — input: word-timed transcript (+ optional reference script); output: take-groups + keep/drop decisions + markers.**

```
STAGE A — SENTENCE / TAKE SEGMENTATION
  A1. Split words into "utterances": break where gap ≥ 0.80 s
      (Tribe uses 1.0 s; FireRed groups at 0.4 s — 0.80 s is our middle ground).
  A2. Discard utterances < 3 words (too short to be a retake) and
      < 0.5 s duration.

STAGE C1 — CHEAP LEXICAL GATE  (kills ~95% of pairs, no ML)
  For each utterance U, compare against the previous utterances in a
  ±60 s window (and against ALL previous if a reference script is present):
      len_ratio   = min(len)/max(len)                       ≥ 0.50
      jaccard     = |A∩B| / |A∪B|  on normalised tokens     ≥ 0.55
      norm_lev    = 1 - lev(a,b)/max(len(a),len(b))         ≥ 0.60
      ngram_hit   = any shared 5-gram (normalised, no spaces) OR
                    shared 4-gram within 15 chars distance
  Pair is a CANDIDATE if  len_ratio OK  AND  (jaccard ≥ 0.55 OR norm_lev ≥ 0.60
                                              OR ngram_hit).
      [Tribe-Video-Cleaner: (jaccard+lev)/2 ≥ sensitivity;  reel-auto-cut: 5/4-gram]

STAGE C2 — SEMANTIC CONFIRMATION  (paraphrase recall, Indic/cross-script safe)
  Embed every utterance once with
      sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  (384-d, Apache-2.0)
  (upgrade to paraphrase-multilingual-mpnet-base-v2 or bge-m3 if recall is short).
  Merge candidates into a cluster when cosine ≥ 0.88.
  Cluster = one "sentence, N takes". Non-adjacent members allowed
  (retakes can be minutes apart), but cap cluster span at 10 minutes.

STAGE B — SAFETY FILTERS  (kill false positives before any cut)
  B1. REFRAIN / DELIBERATE-REPEAT guard:
        if cluster members occur in a repeating pattern (≥3 evenly spaced
        occurrences) OR user allow-list matches → mark "intentional", keep all.
  B2. LENGTH-DISPARITY guard: if min/max duration in cluster > 2.5× →
        probably two different sentences that merely start alike → keep all.
  B3. CONFIDENCE guard: if mean ASR word-probability of any utterance
        < 0.45 → transcript is unreliable (accent/crosstalk) → do NOT auto-cut;
        emit as "needs review".  [EditBuddy failure mode #6]

STAGE D — BEST-TAKE SCORING  (the actual pick)
  For each utterance t in cluster:
    s_conf   = mean(word.probability)                              # 0..1
    s_len    = 1 - |dur(t) - median_dur(cluster)| / median_dur     # 0..1
    s_dnsmos = DNSMOS P.835 OVRL (ONNX via `speechmos`, MIT)       # ~1..5 → (x-1)/4
    s_loud   = 1 - |LUFS(t) - target(-18 LUFS)| / 10               # 0..1
    s_clean  = 1 - (fillers + disfluencies) / word_count(t)        # 0..1
    s_script = 1 - WER(t, reference_line)      if script provided  # 0..1 (jiwer)
    score = 0.30·s_conf + 0.15·s_len + 0.25·s_dnsmos
          + 0.10·s_loud + 0.15·s_clean + 0.05·s_script
  KEEP = argmax(score).
  TIE-BREAK: if top two scores differ by < 5%, keep the LATER take
             (matches TimeBolt's "selects the last cut" + creator intuition);
             otherwise keep the higher score.
  RANK the losers as: [hard-flub] < [false start] < [good but redundant].

STAGE E — CUT DECISION
  For each cluster, drop all non-kept utterances → subtract their ranges
  from keep_ranges (reuse the silence engine's Stage 5/6: margin expand
  0.15 s / 0.35 s, then energy-snap).
  Overlap the retake ranges with the silence ranges (union of CUT ranges),
  then take the complement of the source timeline as the final keep_ranges.
  Re-apply padding ONCE, last.

STAGE F — QC GATE (deterministic, zero-ML, must pass before render)
  Run a reel-auto-cut-style scan on the final transcript:
      global 5-gram re-occurrence + near-distance (<15 chars) 4-gram
      + allow-list for intentional refrains.
  Exit non-zero → route the file to "needs review" instead of rendering.

STAGE G — DELIVER
  Default: emit MARKERS (soft in/out points) on the timeline for human review
           [Claude+Premiere pattern]; cut only on user approval.
  Bulk/unattended mode: cut directly, but write an audit JSON:
      {cluster_id, kept_take, dropped:[{range, reason, score}], thresholds}
      so every automated decision is explainable and undoable.

OPTIONAL STAGE H — LLM ARBITER (offline: Ollama; contested clusters only)
  Feed FireRed's `speech_rough_cut` prompt shape: current utterance + prev/next
  context + prior decisions → keep/drop JSON, temperature 0.1.
  Use ONLY where C1/C2 disagree or score margin < 5%.
```

**What to build vs. what to reuse**

| Piece | Action |
|---|---|
| `keep_ranges` bookkeeping, margin expand, energy snap, ffmpeg render, EDL export | **Build** (shared with silence + filler — one engine) |
| Sentence segmentation + lexical gate + scoring weights | **Build**, porting the algorithm from `grafup/Tribe-Video-Cleaner` (re-implement — repo is **unlicensed**) and `timkulbaev/ai-video-editor` (**MIT**, so it may be vendored with attribution) |
| Timestamp re-map after deletions | **Vendor** `calibrate_asr_times()` from FireRed-OpenStoryline (**Apache-2.0** — compatible) |
| Post-cut duplicate QC | **Vendor/adapt** `repeat_scan.py` from `ronaldtsecf/reel-auto-cut` (**MIT**) |
| NLE export | **Vendor** auto-editor (**Unlicense**) or shell out to it |
| Embeddings, ASR, VAD, DNSMOS | **Depend**: sentence-transformers (Apache-2.0), faster-whisper (MIT), silero-vad (MIT), speechmos (MIT) |
| LLM arbiter | Optional; Ollama local model, or skip in v1 |

**v1 scope cut (ship this first):** Stages A + C1 + D (conf/len/loud/clean only) + E + F + G-markers. Skip C2 embeddings, DNSMOS, and the LLM until we have real user footage showing paraphrased retakes are common enough to justify them. That v1 is ~400 lines of Python and covers the TimeBolt-class use case.

**Explicit non-goals for v1:** auto-cutting without a review pass; paraphrase-level retake recall; multi-speaker/diariozed footage (single talking head only).

---

### 6.3 FILLER REMOVAL — concrete algorithm

```
 1. Same word clock as §6.1/§6.2 (faster-whisper, verbatim prompt,
    condition_on_previous_text=False, language forced).
 2. Match fillers over a sorted-longest-first list, windowed over consecutive
    words, ignoring case/punctuation.
 3. Keep a filler only if duration ≥ 0.30 s.
 4. SAFETY: skip (→ "needs review") if
        • filler_start - prev_word_end < 80 ms  or
        • next_word_start - filler_end   < 80 ms   (would clip a real word)
        • removal leaves < 100 ms of context on either side.
 5. Subtract filler ranges from keep_ranges (split, don't drop the sentence).
 6. Long-tail fillers (Hinglish, sentence-initial restarts) → local LLM pass
    (CutScript pattern) marking candidate word ranges for user confirmation.
 7. Padding (0.15/0.35) applied ONCE after silence+filler+retake ranges are
    unioned; re-merge overlaps; energy-snap; render.
```

**Seed lexicon:** `words/fillers` (MIT) for English + a curated Hinglish list (`matlab, toh, acha, yaar, basically, you know, i mean, so, actually, wait, like, um, uh, hmm, mhm`).

---

### 6.4 Final dependency shortlist (all permissive, all pip-installable, GPU optional)

```
silero-vad            MIT        detection (language-agnostic → Indic-safe)
faster-whisper        MIT        ASR + word clock (+ built-in Silero VAD)
whisperx              BSD-2      optional forced alignment for word boundaries
sentence-transformers Apache-2.0 paraphrase-multilingual-MiniLM-L12-v2 (384-d)
speechmos             MIT        DNSMOS P.835 ONNX → no-reference audio quality
jiwer                 MIT        WER vs. creator's reference script (optional)
scikit-learn          BSD-3      optional clustering (greedy path needs none)
auto-editor           Unlicense  baseline + NLE timeline export (shell out)
ffmpeg                LGPL/GPL   render: trim/atrim/concat + 15 ms afade
python-Levenshtein    MIT        fast normalized edit distance (or pure-python)

AVOID: rescript (PolyForm Noncommercial), Concat (AGPL-3.0),
       jumpcutter (unmaintained), video-silence-remover (no license),
       Tribe-Video-Cleaner / kocut (no license — algorithms only, re-implement),
       PESQ reference code (commercial-use restriction → use DNSMOS instead)
```
