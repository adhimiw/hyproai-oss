# 03 — ASR with Word-Level Timestamps (Indian languages)

**Feature:** raw talking-head / podcast video in **Tamil, Telugu, Hindi, Malayalam, Kannada, Bengali, English** (frequently code-switched) → transcript with **word-level timestamps** → downstream stages (segmentation, subtitle burn-in, romanization, clip highlight).

**Target workload:** ~40-min episode, **batch, local/offline**, pip- or uv-installable, **GPU optional (CPU must work)**, permissive license preferred, **no paid API required**.

**Research method:** web search (GitHub, Hugging Face, arXiv/ISCA, blogs, Reddit), direct pulls of README/pyproject/LICENSE from raw GitHub + PyPI JSON + HF API. Popularity/release figures captured **Nov 2026**. Numbers marked *(measured here)* are from the upstream benchmark tables or papers cited inline — we did not re-run ASR on this machine for this doc.

---

## 0. Headline findings (read this first)

| # | Finding | Why it matters |
|---|---|---|
| 1 | **WhisperX** (m-bain, BSD-2, 25.1k★) is the strongest OSS stack for our job: it wraps **faster-whisper** (fast transcription) + **wav2vec2 forced alignment** (sharp word boundaries) + pyannote (diarization). Peer-reviewed: **93.2% word-segmentation precision @200 ms vs 85.4%** for Whisper's own timestamps (Switchboard); 84.1% vs 78.9% (AMI). | Word timestamps are a *second* model's job, not Whisper's. WhisperX does exactly this. |
| 2 | **The catch:** WhisperX's default aligners exist only for `hi`, `te`, `ml`, `ur` (+ `en`-family). **No default for `ta`, `kn`, `bn`** → you must pass `--align_model <hf-id>` per language. | 3 of our 7 languages need an explicit aligner wired in (supplied below, §4.2). |
| 3 | **Bigger catch:** WhisperX picks **one aligner per file**. Code-switched audio (Tamil↔English in one file) breaks alignment — open issue **#298**, plus accuracy complaints #1247, #1220. | For code-mixed episodes we cannot naively run one aligner over the whole file. Need a per-segment language strategy (§7.3). |
| 4 | **Whisper truncates Indic text.** The 224-token decoder cap costs ~3 tokens per Indic character → transcripts die at ~70–90 chars with `�` (Adalat AI HF blog, Jul 2026; arXiv 2610.08847). Fix = **VAD windows of ~6–7 s** (or raise the cap). | WhisperX's default VAD (30 s) is too coarse for Indic. **Force `vad_parameters=dict(min_silence_duration_ms=…)` / shorter windows.** |
| 5 | **faster-whisper** (SYSTRAN, MIT, 26.5k★) gives *usable* native word timestamps with **zero extra models** (`word_timestamps=True`, cross-attention + DTW) — 4× faster than openai/whisper on CPU *(their table)*. | This is the **fallback** when aligner plumbing gets hairy: good-enough words, no aligner dependency. |
| 6 | **Best raw accuracy for Indic is NOT Whisper.** AI4Bharat **IndicConformer** (MIT, 22 Indic langs) and ARTPARK **SraVaani** (MIT, 65 Indic langs) beat Whisper-family fine-tunes on WER — but **neither has native word timestamps**. | Accuracy and word-timestamps are *separate axes*. Pair a strong Indic model with MFA/CTC alignment (§4). |
| 7 | Licenses to **avoid** for a commercial OSS product: **whisper-timestamped = AGPL-3.0** (repo) / GPLv3 (PyPI), **stable-ts = archived** (dead dependency), **Indic-Transcribe = "Indic Open Model License v1.0"** (share-alike + hosted-API sign-off above 500M MAU / $250M revenue), **MURIL = not ASR at all**, **"Maha-ASR" = does not exist**. | See license table §6. |

**Bottom line:** no single OSS tool gives *both* best Indic accuracy *and* word timestamps out of the box. The working answer is a **two-model pipeline** (§7): a transcriber + a forced aligner.

---

## 1. Evaluation criteria (what we scored against)

1. **Word timestamps**: native / forced-aligned / none.
2. **Language coverage** of ta, te, hi, ml, kn, bn, en — and **code-switching**.
3. **CPU must work** (GPU optional); pip/uv installable; offline after model download.
4. **Permissive license** (MIT / Apache / BSD preferred).
5. Maintenance: stars, last release, open-issue load.

---

## 2. Transcription frameworks (the "engine" candidates)

### 2.1 faster-whisper — **primary engine**

| field | value |
|---|---|
| name / repo | **faster-whisper** — https://github.com/SYSTRAN/faster-whisper |
| license | **MIT** (code); Whisper weights on HF are **apache-2.0** |
| popularity / maintenance | **26,461★**, pushed **2026-11-02** (active); PyPI **1.2.1** (2025-10-31) |
| models & sizes | All Whisper checkpoints re-implemented in **CTranslate2**: tiny 39M / base 74M / small 244M / medium 769M / **large-v3 1.55G** / **large-v3-turbo 809M** (distilled 4-layer decoder) |
| 7-language coverage | ✅ all 7 (Whisper is trained on 99 langs incl. ta/te/hi/ml/kn/bn/en). **But** accuracy on Indic is mediocre out-of-box (see §3) |
| word timestamps | **Native** ✅ — `word_timestamps=True` uses cross-attention weights + DTW over frames, no extra model. Also emits per-word confidence |
| CPU vs GPU | *(their table)* **13-min audio, CPU int8, 8 threads = 1m42s** vs 6m58s openai/whisper → ~4× faster. GPU large-v2 batched = **17 s** for the same clip. Well under real-time on CPU |
| install | `pip install faster-whisper` (or `uv add faster-whisper`); pulls ctranslate2 + tokenizers, **no torch needed** for inference |
| minimal CLI | `whisper-ctranslate2 audio.wav --model large-v3-turbo --language ta --word_timestamps True --output_format json` |
| minimal python | `from faster_whisper import WhisperModel; m = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8"); segs, info = m.transcribe("a.wav", language="ta", word_timestamps=True)` → each `Segment` has `.words` with `.start/.end/.word/.probability` |
| pros | Fastest CPU path; MIT; no torch; native word timestamps w/o aligner; solid community; batched GPU inference |
| cons | Indic accuracy below specialist models; **224-token cap still applies** (§4.1); word boundaries less sharp than forced alignment |

### 2.2 whisperX — **recommended front-end (transcribe + align)**

| field | value |
|---|---|
| name / repo | **whisperX** — https://github.com/m-bain/whisperX |
| license | **BSD-2-Clause** |
| popularity / maintenance | **25,080★**, pushed **2026-10-22**; PyPI **3.8.6** (2026-05-25); ⚠️ **217 open issues** |
| architecture | **faster-whisper** backend (transcription) → **wav2vec2/XLS-R forced alignment** (word timestamps) → optional **pyannote** diarization |
| 7-language coverage | Transcription: all 7 ✅. **Alignment: `hi` ✅ `te` ✅ `ml` ✅ `en` ✅ — `ta` ❌ `kn` ❌ `bn` ❌** (no default aligner) |
| word timestamps | **Forced alignment** — best-in-class sharpness. Paper: **93.2% precision @200 ms** vs 85.4% Whisper-native (Switchboard); 84.1% vs 78.9% (AMI) |
| CPU vs GPU | Backend inherits faster-whisper speed (CPU int8 ≈ 1m42s / 13 min); alignment adds a cheap wav2vec2 pass (CPU fine) |
| install | `pip install whisperX` (torch + pyannote extras come along; heavier than bare faster-whisper) |
| minimal CLI | `whisperX -a a.wav -m large-v3 -l ta --model_framework faster_whisper --output_format json --word_timestamps True` · aligner override: `--align_model <hf-model-id>` |
| minimal python | `import whisperX; a = whisperX.load_align_model(language_code="hi", device="cpu"); r = whisperX.align(segs, a["model"], a["metadata"], "a.wav", "cpu")` → `r["word_segments"]` has `start/end/word` |
| pros | Sharpest word boundaries (proven); combines ASR + align + diarize in one call; BSD; fastest backend; subtitle-ready output |
| cons | **One aligner per file → code-switching breaks** (issue #298); missing default aligners for **ta/kn/bn**; known accuracy bugs (#1247, #1220); 217 open issues; heavier install (torch) |

### 2.3 openai/whisper — **baseline**

| field | value |
|---|---|
| repo | https://github.com/openai/whisper |
| license | **MIT** (code) · weights on HF = **apache-2.0** |
| popularity | **111,908★**, pushed 2026-08-31 (mature/slow-moving) |
| word timestamps | Native ✅ `word_timestamps=True` (same DTW-on-cross-attention idea) |
| CPU speed | **6m58s** for 13-min audio *(faster-whisper's table)* — 4× slower than faster-whisper, same quality |
| install | `pip install -u openai-whisper` (needs **torch + ffmpeg**) |
| use | Reference implementation / fallback only. **Use faster-whisper instead** — same weights, 4× faster, no reason to take the perf hit |
| pros | Canonical, MIT, everything else clones it |
| cons | Slow on CPU; heavy torch dep; same Indic token-cap problem |

### 2.4 stable-ts — ⚠️ **archived, do not depend on**

| field | value |
|---|---|
| repo | https://github.com/jianfch/stable-ts |
| license | MIT |
| status | **ARCHIVED 2026-05-30 — "development indefinitely paused"**; 2,280★; PyPI **2.19.1** (2025-08-16) |
| feature | `word_timestamps` default **True**; `align()` / `align_words()` re-alignment; `pip install stable-ts[fw]` for faster-whisper backend; regulator smoothing for timestamp drift |
| verdict | **⛔ skip** — archived = no bug fixes, no security patches, a liability as a hard dependency in a shipped product. Its smoothing idea is nice, but WhisperX/faster-whisper cover it |

### 2.5 whisper-timestamped — ⚠️ **AGPL, license conflict**

| field | value |
|---|---|
| repo | https://github.com/linto-ai/whisper-timestamped |
| license | **AGPL-3.0** (repo LICENSE) / PyPI metadata reports **GPLv3** |
| popularity | 2,858★, pushed 2026-09-28; PyPI **1.15.9** (2025-09-09) |
| feature | DTW word timestamps **+ per-word confidence, no extra inference cost** (uses Whisper's own attention) |
| verdict | ⛔ **Avoid for a commercial OSS product** — AGPL/GPL would force our whole codebase open. The per-word confidence trick is valuable though — faster-whisper also emits word probabilities, so we get that benefit without the license |

### 2.6 Vosk — ⚠️ **weak Indic coverage**

| field | value |
|---|---|
| repo | https://github.com/alphacep/vosk-api |
| license | **Apache-2.0** |
| popularity | 15,226★, pushed 2026-10-15; ⚠️ **605 open issues**; PyPI `vosk` **0.3.45 last released 2022** (stale wheel) |
| word timestamps | ✅ native — Kaldi emits word timings for free (this is Vosk's genuine strength: tiny models, real-time, offline) |
| Indic models | Hindi `hi-0.22` (1.5 GB, WER 14.85 CV), Indian English `en-in-0.5` (WER **36.12** — poor), Telugu small (87.9 FLEURS — unusable), Gujarati. **❌ No Tamil, Malayalam, Kannada, Bengali** |
| CPU speed | Very light — real-time on old CPUs |
| verdict | ⛔ **Doesn't cover 4 of our 7 languages.** Reconsider only if we later add a real-time/low-power tier |

### 2.7 NVIDIA NeMo — ⚠️ **heavy + license trap**

| field | value |
|---|---|
| repo | https://github.com/NVIDIA-NeMo/NeMo (speech split to `NVIDIA-NeMo/speech`) |
| license | **Apache-2.0** (code) · ⚠️ **many pretrained SE/DER weights = CC-BY-NC-SA-4.0 (non-commercial)** |
| popularity | 18,691★, pushed 2026-11-04 (active); PyPI `nemo-toolkit` **3.0.0** (2026-08-07, requires **Python ≥3.10**) |
| word timestamps | Via **NeMo Forced Aligner** — but only for select models; not a generic per-word service |
| Indic | No strong ready-made Indic ASR checkpoints in the main repo |
| verdict | ⛔ Too heavy for our CPU batch job, and the weight licensing is a minefield. **Exception:** AI4Bharat maintains a *fork* of NeMo (§3.1) which we'd use only as a runtime, not as a product dependency |

### 2.8 pyannote-audio — **adjunct (diarization), not ASR**

| field | value |
|---|---|
| repo | https://github.com/pyannote/pyannote-audio |
| license | **MIT** (code) · weights: `speaker-diarization-3.1` = **MIT (gated=auto)**, `speaker-diarization-community-1` = **CC-BY-4.0 (gated=auto)** |
| popularity | 10,757★, pushed 2026-11-03 (active); models at 6.98M / 5.15M downloads |
| role | **Speaker diarization only** — needed downstream for "who spoke when" (multi-host podcasts), not for transcription |
| note | WhisperX already wires pyannote in as an optional stage; requires accepting HF gating + an HF token |
| verdict | ✅ include as an **optional** stage for multi-speaker episodes; MIT code + MIT 3.1 weights are fine to ship |

---

## 3. Indic-specialised models (the *accuracy* axis)

Whisper-family is general-purpose. For **pure-Indic** accuracy these beat it — but check word-timestamp support carefully.

### 3.1 AI4Bharat IndicConformer

| field | value |
|---|---|
| repo | https://github.com/AI4Bharat/IndicConformerASR |
| license | **MIT** ✅ (code + `ai4bharat/indic-conformer-600m-multilingual` weights) |
| popularity | 106★ (small repo, but the **model** has **183,941 downloads / 262 likes**, updated **2026-10-08**) |
| models | `indic-conformer-600m-multilingual` (hybrid CTC-RNNT, all **22 Indic langs**); monolingual "large" variants per language incl. **ta, te, ml, kn, bn, hi** |
| coverage | ✅ all 7 (incl. `en` where trained) |
| word timestamps | ❌ **none native** → use **NeMo Forced Aligner** (a community workaround is documented in the HF discussion) |
| install | ⚠️ needs the **AI4Bharat NeMo fork**: `git clone https://github.com/AI4Bharat/NeMo && git checkout nemo-v2 && bash reinstall.sh` — not a clean `pip install` |
| pros | MIT, genuinely better Indic WER than Whisper, one model for 22 langs |
| cons | Fork-based install (fragile, pins us to their NeMo), no word timestamps, small repo/issue triage |

### 3.2 IndicWhisper (AI4Bharat) + community mirrors

| field | value |
|---|---|
| what | Whisper **medium fine-tuned on 12 Indian languages / 10.7K hours** |
| primary HF id | ⚠️ `ai4bharat/indic-whisper` returns **401** (gated/absent) |
| usable mirrors | `parthiv11/indic_whisper_nodcil`, `kavyamanohar/AI4B-Indicwhisper-ml` — **MIT** per model card |
| word timestamps | Inherited from Whisper: native `word_timestamps` ✅ (same DTW approach, same token-cap caveat) |
| verdict | ✅ **Good middle option** — Whisper-compatible API (drop-in for faster-whisper-style code paths) + better Indic accuracy, MIT, via mirrors |

### 3.3 vasista22 per-language Whisper fine-tunes — **cleanest per-language pick**

| field | value |
|---|---|
| org | vasista22 on HF |
| license | **Apache-2.0** ✅ |
| models | `whisper-tamil-small` (**127,551 downloads** — the flagship), `whisper-telugu-*`, `whisper-hindi-*`, `whisper-kannada-*`, `whisper-gujarati-*` |
| word timestamps | ✅ native (Whisper architecture) |
| ⚠️ constraint | **Monolingual** — each checkpoint is one language, so **code-switched audio degrades** (it will still transcribe, but the English side suffers) |
| verdict | ✅ Best **Apache-2.0**, Whisper-compatible, high-download option for the *pure-language* path. Note: no Malayalam/Bengali in this set |

### 3.4 ARTPARK-IISc Vaani-Whisper + SraVaani

| field | value |
|---|---|
| org | ARTPARK-Speech-Models (IISc) |
| Vaani-Whisper license | **Apache-2.0** ✅ |
| Vaani models | `whisper-large-v3-vaani-hindi` (10,930 dl, **2B params**), `-vaani-telugu`, `-vaani-odia`; `whisper-medium/small-vaani-{kannada,bengali,hindi,telugu,tulu}` — **covers kn, bn, hi, te** ✅ |
| word timestamps | ✅ native (Whisper arch) |
| **SraVaani-1.0** | **MIT** ✅ · ~**430M** FastConformer hybrid TDT-CTC · ~900 MB fp16 · **65 Indic languages** · `trust_remote_code` · 11,526 dl |
| SraVaani WER | hi **12.4** / bn **19.8** / te 25.1 / ta 26.2 / ml 27.7 / kn 27.4 / en 14.8 — **clearly better than Whisper on Indic** |
| SraVaani-0.5-live | MIT, **122,990 downloads** (the popular variant); fine-tune repo `github.com/ARTPARK-Speech-Models/SraVaani` |
| word timestamps (SraVaani) | ❌ none native → needs forced alignment (MFA / CTC) |
| verdict | ✅ **SraVaani = best MIT accuracy-per-GB for Indic**, 65 languages in one ~900 MB model. Pair with an aligner (§4) |

### 3.5 Indic-Transcribe — best at code-mixing, ⚠️ **license**

| field | value |
|---|---|
| org | Bodhan AI / AI4Bharat |
| HF ids | `IAMCB/indic-transcribe-core`, `bodhan-ai/indic-transcribe-flex` |
| model | **1.2B Canary FastConformer**, 25–27 languages, **native code-mixing support** |
| benchmark | **OIWER avg 8.7** — best in the Voice-of-India table (vs Vaani 16.6% / Whisper API 46.8% on the Hindi/Telugu/Odia benchmark, arXiv 2602.03868) |
| CPU option | ONNX variant `abid1010/indic-transcribe-core-onnx` ✅ |
| license | ⚠️ **"Indic Open Model License v1.0"** — share-alike; **requires sign-off for hosted API use** above **500M MAU / $250M revenue** thresholds. **Not fully permissive** |
| word timestamps | ❌ none native |
| verdict | ⚠️ **Technically the best code-mixed transcriber we found, but the license needs legal review** before we build on it. Keep as a research/fallback option, gate behind a decision |

### 3.6 Dolphin (via sherpa-onnx) — real-time tier, partial coverage

| field | value |
|---|---|
| repo | https://github.com/k2-fsa/sherpa-onnx (Dolphin models on HF) |
| license | **Apache-2.0** ✅ |
| popularity | **15,724★** (active) |
| coverage | Dolphin supports **hi, ta, te, gu, pa, mr** — ⚠️ **missing `kn`, `ml`, `bn`** (3 of our 7) |
| word timestamps | ✅ timestamps available through sherpa-onnx decoding |
| install | `pip install sherpa-onnx` (ONNX Runtime, no torch) — very CPU-friendly |
| verdict | ⚠️ Nice for a future **real-time/streaming** tier, but incomplete language coverage for v1 |

### 3.7 distil-whisper — ❌ English only

`distil-whisper/distil-large-v3` is **MIT** but its model card declares `language: [en]` — **English-only**. Unusable for our Indic requirement. (Only relevant if we later need a cheap English-only fast path.)

### 3.8 Dead ends (documented so we don't re-research them)

| candidate | verdict |
|---|---|
| **MURIL** (`google/muril-base-cased`) | ❌ **Not an ASR model** — it's a **BERT-style NLU encoder** (masked-LM, Apache-2.0). Cannot transcribe audio at all |
| **"Maha-ASR"** | ❌ **Does not exist** — no matching project on GitHub or Hugging Face. Likely a garbled reference (possibly "Maha"→Marathi, or a confusion with another name) |
| **stable-ts** | ❌ archived (§2.4) |
| **whisper-timestamped** | ❌ AGPL (§2.5) |
| **Vosk** | ❌ missing 4/7 languages (§2.6) |
| **distil-whisper** | ❌ English-only (§3.7) |

### 3.9 Code-switching options (Tamil↔English etc.)

| option | coverage | license | note |
|---|---|---|---|
| **Indic-Transcribe** | ✅ native code-mix | ⚠️ Indic Open Model License | Best accuracy; license review needed |
| `Trelis/whisper-hinglish-preview` | Hindi↔English | check card | Preview/experimental |
| `Oriserve/Whisper-Hindi2Hinglish-*` | Hindi↔English | check card | Transliteration-oriented |
| **Stock Whisper / faster-whisper** | partial | MIT/apache-2.0 | Handles code-switch *decently* for transcription (it's multilingual by design) — **but alignment then breaks** (§4.3) |
| Per-language fine-tunes (vasista22, Vaani) | ❌ monolingual | Apache-2.0 | Will mangle the other language's side |

**Reality check:** Whisper-family transcription survives code-switching reasonably; **the forced-aligner step is what breaks.** That's the problem to solve in §7.3.

---

## 4. Word timestamps in depth (the crux)

### 4.1 Two mechanisms

| mechanism | how | sharpness | cost | used by |
|---|---|---|---|---|
| **Native (DTW on attention)** | Whisper's cross-attention weights → DTW to frame boundaries | Good — **85.4% precision @200 ms** (Switchboard) | Free (same inference) | faster-whisper, openai/whisper, stable-ts, whisper-timestamped, vasista22/Vaani fine-tunes |
| **Forced alignment** | A *separate* CTC/wav2vec2 model aligns the *known* transcript back onto audio | **Best — 93.2% @200 ms** | One extra cheap pass + an aligner model per language | **whisperX**, MFA, NeMo Forced Aligner |

**Why alignment wins:** the transcriber already knows *what* was said; the aligner only has to answer *when*. That's an easier problem, and it's language-specific in a good way (a Tamil wav2vec2 knows Tamil phoneme durations).

### 4.2 Aligner availability for our 7 languages

| lang | whisperX default aligner | alternative aligners (HF) |
|---|---|---|
| `hi` Hindi | ✅ `theainerd/Wav2Vec2-large-xlsr-hindi` | MFA Hindi; Vakyansh hi |
| `te` Telugu | ✅ `anuragshas/wav2vec2-large-xlsr-53-telugu` | MFA Telugu |
| `ml` Malayalam | ✅ `gvs/wav2vec2-large-xlsr-malayalam` | MFA Malayalam |
| `en` English | ✅ default en aligner | MFA English (LibriSpeech) |
| **`ta` Tamil** | ❌ **none** | `Harveenchadha/vakyansh-wav2vec2-tamil-tam-250` (**MIT**) · MFA Tamil |
| **`kn` Kannada** | ❌ **none** | `Harveenchadha/vakyansh-wav2vec2-kannada-knm-560` (⚠️ **no license file**) · MFA Kannada |
| **`bn` Bengali** | ❌ **none** | `arijitx/wav2vec2-xls-r-300m-bengali` (**Apache-2.0**) · MFA Bengali |
| (en-in) Indian English | — | `Harveenchadha/vakyansh-wav2vec2-indian-english-enm-700` |

Also useful: **AI4Bharat/IndicMFA** — letter-wise (G2G) forced aligners for **all 22 Indic languages** (dictionaries + acoustic models on its releases page). ⚠️ Caveats: repo license = **not specified**, only **21★**, last push **2024-09-13** (stale).

### 4.3 The two Indic-specific failure modes

**(a) Token-cap truncation.** Whisper's decoder stops at 224 new tokens, and Indic scripts burn ~3 tokens per character → output cut at ~70–90 chars with `�` (Adalat AI blog, Jul 2026; arXiv 2610.08847).
→ **Fix: VAD windows of ~6–7 s** (or raise the cap). WhisperX's default 30 s windows are too long for Indic — **shorten them.**

**(b) One aligner per file (whisperX #298).** A Tamil+English episode can't be aligned by a single Tamil wav2vec2.
→ **Fix: segment-first alignment** — split by detected language, align each run with its own model (§7.3).

**(c) Bonus: CTC timestamps.** For Hindi specifically, `Sanat-agrwl/indic-whisper-ctc-timestamps` (Apache-2.0) bolts a CTC head onto Whisper-large-v2 for **20 ms boundaries**, mean error **74.5 ms** vs 27 ms human IAA (arXiv 2610.08847), with a `--calibrate` flag. Hindi-only — worth watching, not a general solution.

---

## 5. Speed summary (CPU-first)

| stack | 13-min audio, CPU | GPU | notes |
|---|---|---|---|
| **faster-whisper** large-v3 int8, 8 threads | **1m42s** *(upstream)* | 17 s (large-v2 batched) | 4× openai/whisper |
| openai/whisper | **6m58s** *(upstream)* | — | baseline |
| whisperX | ≈ faster-whisper **+** cheap wav2vec2 align pass | same + align | align is minor vs transcription |
| Vosk | real-time or better | n/a | tiny models (but missing langs) |
| sherpa-onnx / Dolphin | real-time, ONNX CPU | optional | incomplete coverage |
| SraVaani / IndicConformer | not benchmarked here | GPU recommended | ~430M–600M, CPU viable but slower |

**Implication for a 40-min episode on CPU:** faster-whisper ≈ **5–7 min**; whisperX adds little. Comfortable for a batch job.

---

## 6. License / compliance table

| component | license | ship? |
|---|---|---|
| **faster-whisper** | MIT | ✅ |
| Whisper weights (HF) | apache-2.0 | ✅ |
| **whisperX** | BSD-2-Clause | ✅ |
| openai/whisper | MIT | ✅ |
| pyannote-audio (code) | MIT | ✅ |
| pyannote `speaker-diarization-3.1` | MIT (gated) | ✅ (accept gating) |
| pyannote `community-1` | CC-BY-4.0 | ✅ w/ attribution |
| **vasista22 fine-tunes** | Apache-2.0 | ✅ |
| **Vaani-Whisper** | Apache-2.0 | ✅ |
| **SraVaani** (0.5 & 1.0) | MIT | ✅ |
| **IndicConformer** + weights | MIT | ✅ (⚠️ fork-based install) |
| IndicWhisper mirrors | MIT | ✅ (⚠️ primary id gated) |
| sherpa-onnx / Dolphin | Apache-2.0 | ✅ (partial coverage) |
| Vakyansh `ta` aligner | MIT | ✅ |
| `arijitx/...bengali` aligner | Apache-2.0 | ✅ |
| MFA aligners/models | mostly MIT/Apache — **verify per model** | ⚠️ per-model check |
| Vosk | Apache-2.0 | ✅ (but missing langs) |
| **whisper-timestamped** | **AGPL-3.0 / GPLv3** | ⛔ |
| **stable-ts** | MIT but **archived** | ⛔ (dead dep) |
| **Indic-Transcribe** | **Indic Open Model License v1.0** (share-alike + API sign-off) | ⚠️ legal review |
| NeMo weights (many) | **CC-BY-NC-SA-4.0** | ⛔ non-commercial |
| Vakyansh `kn` aligner | **no license file** | ⚠️ avoid bundling |
| AI4Bharat/IndicMFA | **not specified** | ⚠️ review |

---

## 7. RECOMMENDED PICK

### 7.1 The architecture: transcriber + aligner (two models, not one)

```
audio → VAD (6–7s windows) ─┬─► faster-whisper (large-v3-turbo) ──► transcript
                             │        (MIT, native word_timestamps = FALLBACK path)
                             │
                             └─► whisperX (faster-whisper backend)
                                       └─► per-language wav2vec2 forced alignment  ← PRIMARY path
                                       └─► (optional) pyannote diarization
```

**Primary = whisperX. Fallback = faster-whisper alone. Free-API fallback = Groq.** Plus an **accuracy tier** using SraVaani/IndicConformer when raw WER matters more than pipeline simplicity.

### 7.2 Primary: whisperX

```bash
# one-time setup
uv venv && source .venv/bin/activate
uv pip install whisperX

# Tamil (no default aligner → pass one explicitly)
whisperX -a episode.wav -m large-v3-turbo -l ta \
         --model_framework faster_whisper \
         --align_model Harveenchadha/vakyansh-wav2vec2-tamil-tam-250 \
         --output_format json --word_timestamps True

# Hindi / Telugu / Malayalam → defaults work, no --align_model needed
whisperX -a episode.wav -m large-v3-turbo -l hi --output_format json
```

**Language → aligner wiring (ship this as a config table):**

| `--language` | `--align_model` |
|---|---|
| `hi`, `te`, `ml`, `en` | *(whisperX default — omit the flag)* |
| `ta` | `Harveenchadha/vakyansh-wav2vec2-tamil-tam-250` (MIT) |
| `bn` | `arijitx/wav2vec2-xls-r-300m-bengali` (Apache-2.0) |
| `kn` | `Harveenchadha/vakyansh-wav2vec2-kannada-knm-560` (⚠️ no license — **prefer MFA Kannada or retrain**) |

**Critical Indic settings** — shorten VAD windows so Whisper's 224-token cap doesn't truncate Tamil/Telugu/etc.:
```python
import whisperX
vad_options = {"min_silence_duration_ms": 500, "max_speech_duration_s": 7}  # ~6–7s windows
```

**Install note:** whisperX pulls torch — heavier than bare faster-whisper. CPU is still fine (alignment is a cheap pass).

### 7.3 Handling code-switched episodes (the hard case)

WhisperX aligns with **one** model per file (issue #298). Workaround, in priority order:

1. **Detect language per segment** (faster-whisper's `info.segments[].language`, or the language-probability stream), **group consecutive same-language segments**, then call `whisperX.align()` **once per group with that group's aligner**. Word timestamps then merge back in time order. ~30 lines of glue.
2. **If only `en`↔one Indic language** and accuracy is acceptable → align the whole file with the **Indic** aligner (it handles English loanwords poorly, but boundaries stay sane).
3. **If code-mixing quality is critical** → evaluate **Indic-Transcribe** (native code-mix, best OIWER 8.7) — but **only after legal sign-off** on its license; then align its output with MFA.
4. **Cheap safety net:** fall back to **faster-whisper `word_timestamps=True`** for the whole file when alignment produces outliers (missing/overlapping words) — it needs no aligner at all.

### 7.4 Fallback: faster-whisper (zero aligner dependency)

```python
from faster_whisper import WhisperModel

model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
segments, info = model.transcribe(
    "episode.wav",
    language="ta",              # or "hi","te","ml","kn","bn","en"
    word_timestamps=True,       # native DTW — no aligner model needed
    vad_filter=True,
    vad_parameters=dict(min_silence_duration_ms=500, max_speech_duration_s=7),  # Indic token-cap fix
)
for seg in segments:
    for w in seg.words:
        print(f"{w.start:7.2f} {w.end:7.2f}  {w.word}  p={w.probability:.2f}")
```
- **When to use:** alignment plumbing fails, a language lacks a vetted aligner, or we want the lightest install (no torch).
- **Trade-off:** word boundaries ~4–8 % less sharp than forced alignment (85.4% vs 93.2% precision @200 ms), but **per-word confidence comes free**.

### 7.5 Accuracy tier (optional, when WER > speed)

For **pure-Indic** episodes where transcript correctness beats pipeline simplicity:

```python
# SraVaani (MIT, ~900MB, 65 Indic langs, best WER in our set)
#   hi 12.4 · bn 19.8 · te 25.1 · ta 26.2 · ml 27.7 · kn 27.4
#   → transcribe, then force-align the text with MFA / wav2vec2 for word timings
```
or per-language **vasista22 / Vaani-Whisper** (Apache-2.0, Whisper-compatible → `word_timestamps=True` works natively).
**Constraint:** these are **monolingual** → route only **non-code-switched** files here (detect code-mixing first, fall back to §7.2/§7.4).

### 7.6 Free-API fallback (not for the default path)

**Groq `whisper-large-v3` / `-turbo`** free tier — useful as a **verification/overflow** channel, not the primary:
- 20 RPM · 2000 req/day · 7200 audio-sec/hour (28800/day) · 25 MB uploads
- `timestamp_granularities=["word","segment"]` ✅ word-level out of the box
- ⚠️ **Violates our "local/offline" constraint** → keep it behind a flag (e.g. `ASR_BACKEND=groq`), never default; no key = no feature loss.

### 7.7 Decision rules (encode these)

| situation | route |
|---|---|
| Default for all 7 languages | **whisperX** + per-language aligner table (§7.2) |
| Indic token-cap truncation observed | shorten VAD to **6–7 s** (both paths) |
| Code-switched file | segment-wise alignment (§7.3.1); if messy → faster-whisper fallback |
| Language with no vetted aligner (`kn` license gap) | faster-whisper `word_timestamps=True`, or MFA |
| Pure-Indic, accuracy-critical | **SraVaani** or **vasista22/Vaani** + forced alignment |
| Need diarization (multi-host) | add **pyannote 3.1** (MIT, gated) as an optional stage |
| Alignment output looks wrong | auto-fallback to faster-whisper native timestamps |
| Offline / no-key guarantee | **never** enable Groq by default |

---

## 8. Open questions / risks

1. **`kn` aligner licensing** — the Vakyansh Kannada model has **no LICENSE file**. Options: use MFA Kannada, find an Apache/MIT Kannada aligner, or fine-tune our own from an MIT wav2vec2. **Must resolve before shipping Kannada word timestamps.**
2. **Code-switched alignment is unsolved in whisperX** (issue #298, open). The segment-grouping workaround (§7.3.1) needs real-episode validation — plan a test set of 5–10 code-mixed clips with human-checked word boundaries.
3. **Indic word-timestamp ground truth is scarce** — the 93.2%/85.4% numbers are Switchboard/AMI (English). **We should build a small labelled Indic set** (~10 min × 7 languages) to measure *our* alignment error, not inherit English benchmarks.
4. **WhisperX's 217 open issues** — triage which affect us (esp. #1247, #1220 accuracy, #298 code-switch) before committing.
5. **Indic-Transcribe license** — if code-mixing accuracy proves decisive, get a legal read on "Indic Open Model License v1.0" (share-alike + hosted-API sign-off thresholds).
6. **Model download size** — large-v3-turbo (809 MB) + per-language aligners (~360 MB each) + pyannote (~300 MB). For a bundled offline installer, decide whether to ship all 7 aligners (~2.5 GB) or lazy-download.

---

## 9. Sources

- faster-whisper README + PyPI (`SYSTRAN/faster-whisper`, MIT) — benchmark table, `word_timestamps` docs
- whisperX README + paper (m-bain/whisperX, BSD-2) — alignment precision numbers, aligner matrix, issues #298 / #1247 / #1220
- stable-ts repo (archived 2026-05-30), whisper-timestamped repo (AGPL-3.0)
- HF model cards: `ai4bharat/indic-conformer-600m-multilingual`, `parthiv11/indic_whisper_nodcil`, `vasista22/*`, `ARTPARK-Speech-Models/*` (Vaani/SraVaani), `IAMCB/indic-transcribe-core`, Vakyansh aligners, `arijitx/wav2vec2-xls-r-300m-bengali`
- Adalat AI HF blog (Jul 2026) + arXiv **2610.08847** (Indic token cap, CTC timestamps); arXiv **2602.03868** (Hindi/Telugu/Odia ASR benchmark)
- AI4Bharat/IndicMFA releases; pyannote-audio model cards; k2-fsa/sherpa-onnx (Dolphin); Vosk model list
- Groq API docs (free-tier limits, `timestamp_granularities`)
