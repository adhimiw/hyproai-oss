# 04 — Romanization Layer: Indic Script → Colloquial Latin (Thanglish/Hinglish/Telugish/…)

**Research date:** 2026-11-04 · **Scope:** best open-source approach to turn Indic-script transcripts into
conversational, code-switched Latin captions (HyproAI "Text Magic").
**Target languages:** Tamil, Hindi, Telugu, Malayalam, Kannada, Bengali.
**Constraints:** local/offline preferred, pip-installable, permissive license (MIT/Apache/BSD; avoid
NC/AGPL if possible).

Several candidates were **installed and executed locally** (Python 3.14, CPU) on this machine — those
results are marked ✅ tested and include real outputs.

---

## 0. Framing: what "Text Magic" actually requires

| Requirement | Why it matters | Consequence for architecture |
|---|---|---|
| **Naturalness over reversibility** | Captions must read like typed chat ("kya kar rahe ho"), not ISO-15919/IAST ("kya kar rahā ho"). Research consensus: *deterministic schemes are lossless but unnatural; learned/natural romanizations are lossy but colloquial* (RomanSetu, ACL 2024). | Do **not** ship ISO/ITRANS/IAST as the visible output. Use them only as an internal pivot. |
| **Code-switching safety** | English words already in Latin must never be re-transliterated; English words ASR wrote *in Devanagari* (रेडी for "ready") must ideally stay English. | Token classification gate **before** any conversion; Latin-in → Latin-out, always. |
| **Per-word consistency** | Same word must render identically in every caption of a video; user fixes must persist. | Deterministic word-level pipeline + persistent user glossary; no free-running generation in the hot path. |
| **Speed** | Captions are word-timed; conversion happens per subtitle cue. | Rules/lexicon ≈ µs; small neural ≈ 15–25 ms/sentence; LLM only as offline batch pass. |
| **Offline + permissive** | Local-first product, open-source clone. | AGPL and NC assets flagged below. |

**Lossless vs natural — research grounding**
- *RomanSetu / "R-S"* (ACL 2024, arXiv 2401.14280): deterministic schemes (extended ITRANS from IndicNLP) are reversible; IndicXlit-style "as commonly typed" romanization is **lossy but better for downstream tasks** — chosen by the authors. Directly validates our naturalness-first choice.
- *Aksharantar* (EMNLP Findings 2023): 26M native↔Roman word pairs, 21 languages; the corpus behind IndicXlit.
- *Sentence-Level Back-Transliteration* (LREC 2026): sentence-level beats word-level for context; ≤5-word sentences reach BLEUc ≈ 84; irregular spelling, vowel omission, code-mixing are the hard cases.
- *Beyond Specialization* (arXiv 2505.19851): general LLMs vs IndicXlit on Dakshina/Aksharantar — specialized 11M model still competitive at word level; prompts documented (see §4).
- *LLMs as Transliteration Normalizer* (VarDial 2025): LoRA-tuned small open LLMs normalize Dakshina romanized text — the "LLM fine-tune" option has a paper trail.

---

## 1. Candidate catalog — rule/scheme-based libraries

### 1.1 Aksharamukha — `virtualvinodh/aksharamukha-python`
- **Repo:** https://github.com/virtualvinodh/aksharamukha-python (web app: https://github.com/virtualvinodh/aksharamukha)
- **License:** **AGPL-3.0** (PyPI metadata; site footer says GPL-3.0) — ⚠️ copyleft, conflicts with "permissive" constraint for a distributed product.
- **Maintenance:** 64★ (python lib, last push 2026-08-30), 222★ (web). Single maintainer.
- **Coverage:** all 6 target languages ✅ (121 scripts, 21 romanization schemes).
- **Install:** `pip install aksharamukha`
- **Snippet:**
  ```python
  from aksharamukha import transliterate as ak
  ak.process("Devanagari", "RomanColloquial", "नमस्ते दुनिया कैसे हो")
  # 'namaste duniya kaise ho'
  ak.process("Tamil", "RomanColloquial", "நீங்கள் எப்படி இருக்கிறீர்கள்")
  ```
- **✅ tested locally (real outputs):**
  | Input | RomanColloquial | Natural target | Verdict |
  |---|---|---|---|
  | `नमस्ते दुनिया कैसे हो` | `namaste duniya kaise ho` | same | good |
  | `मुझे नहीं पता कि वो कहाँ गया` | `mujhe nahim pata ki vo kaham gaya` | `mujhe nahi pata ki wo kahan gaya` | ❌ "nahim/kaham" |
  | `यह एक video है जो YouTube पर है` | `yaha eka video hai jo YouTube para hai` | `yeh ek video hai jo YouTube par hai` | ⚠️ Latin words pass through ✅, else stiff |
  | `நீங்கள் எப்படி இருக்கிறீர்கள்` | `ninkal eppati irukkirirkal` | `neenga epdi irukkeenga` | ❌ dictionary-form |
  | `మీరు ఎలా ఉన్నారు` | `miru ela unnaru` | `meeru ela unnaru` | ✅ close |
  | `സുഖമാണോ` | `sukhamano` | `sukhamano` | ✅ |
  | `ನೀವು ಹೇಗಿದ್ದೀರಿ` | `nivu hegiddiri` | `neevu hegiddeera` | ⚠️ |
  | `আপনি কেমন আছেন` | `apani kemana achhena` | `apni kemon achen` | ⚠️ |
- **Py3.12+ bug:** `from ast import Str` in `PreProcess.py` crashes import on Python ≥3.12 (I had to patch it to run). Maintenance risk.
- **Pros:** broadest scheme coverage incl. two "human" modes (`Roman (Colloquial)`, `Roman (Readable)`), orthographic post-options (schwa/nasalization flags), pure Python, fast.
- **Cons for captions:** output is *phonemic scheme output*, not attested chat spelling; no lexicon; AGPL; py3.12+ broken. Best used **only** as a fallback mapping engine or for scheme pivot (Devanagari→ITRANS), not as the final voice.

### 1.2 indic-transliteration / `sanscript` (IIT Bombay lineage)
- **Repo:** https://github.com/indic-transliteration/indic_transliteration_py
- **License:** **MIT** ✅ · **Stars:** 214 · **pushed 2026-10-16** (actively maintained)
- **Coverage:** Devanagari, Bengali, Tamil, Telugu, Malayalam, Kannada + Gurmukhi/Gujarati/Oriya; schemes: HK, ITRANS, IAST, ISO-15919, Velthuis, SLP1, WX, Kolkata, optitrans; also `xsanscript` Dravidian extension.
- **Install:** `pip install indic-transliteration`
- **✅ tested:**
  ```python
  from indic_transliteration import sanscript
  sanscript.transliterate("नमस्ते दुनिया", sanscript.DEVANAGARI, sanscript.ITRANS)
  # 'namaste duniyA'   <- capital A = long vowel: not caption material
  sanscript.transliterate("kaise ho", sanscript.HK, sanscript.DEVANAGARI)  # कैसे हो
  ```
- **Pros:** MIT, zero deps, rock-solid lossless scheme pivot (this is the industry-standard *internal* IR), CLI (`sanscript --from hk --to iast`), per-word deterministic.
- **Cons:** all output is academic (uppercase vowel markers, diacritics). **Not** a caption voice. Use as the reversible backbone (native ⇄ ITRANS/HK) for round-trip checks and glossary indexing.

### 1.3 Indic NLP Library (AI4Bharat/Anoop Kunchukuttan)
- **Repo:** https://github.com/anoopkunchukuttan/indic_nlp_library · PyPI `indic-nlp-library`
- **License:** **MIT** ✅ · **Stars:** 654 (largest star count in this space) · last push 2024-06 (stable/dormant)
- **Coverage:** all 6 + 11 Indic languages; provides *script conversion*, **romanization (extended ITRANS, reversible)** and `IndicNormalizerFactory`.
- **Install:** `pip install indic-nlp-library` (+ separate `indic_nlp_resources` repo for some modules)
- **Snippet:**
  ```python
  # CLI: python src/indicnlp/transliterate/unicode_transliterate.py romanize <in> <out> hi
  from indicnlp.transliterate.unicode_transliterate import UnicodeIndicTransliterator
  UnicodeIndicTransliterator.transliterate("नमस्ते", "hi", "en")  # extended-ITRANS romanization
  ```
- **Pros:** MIT, the exact scheme the RomanSetu paper calls "fixed, reversible mapping"; normalization (numbers, danda) bundled.
- **Cons:** extended-ITRANS is lossless but stiff (`duniyA`-style); resource-path setup is clunky; dormant since 2024.

### 1.4 `libindic/indic-trans`
- **Repo:** https://github.com/libindic/indic-trans · **License:** **AGPL-3.0** ⚠️ · 276★ · last push 2022
- IAST-style Indic↔Latin used as data-prep in published work (IndoTranslit dataset built 1.66M pairs with it).
- **Verdict:** AGPL + dormant → skip for the product; note only as data-prep precedent.

### Scheme quick-reference (lossless family: ISO 15919, ITRANS, Harvard-Kyoto, Velthuis, SLP1, WX)
| Scheme | ASCII? | Reversible | Caption-usable? |
|---|---|---|---|
| ISO 15919 | no (diacritics) | yes (mostly) | no |
| ITRANS | yes | yes | no (`duniyA`) |
| Harvard-Kyoto | yes | yes | no (case-encoded) |
| Velthuis | yes | yes | no (`duniyaa` — closer, but still scheme-y) |
| SLP1 / WX | yes | yes | no (internal codes) |
| **Aksharamukha Roman (Colloquial/Readable)** | yes | partial | **closest scheme output**, still not chat-natural |
| **IndicXlit / lexicon spellings** | yes | **no (lossy)** | **yes — this is what people type** |

---

## 2. Candidate catalog — neural transliteration (native → Roman)

### 2.1 IndicXlit (AI4Bharat) + CTranslate2 port ⭐ core neural option
- **Repo:** https://github.com/AI4Bharat/IndicXlit · **License:** **MIT** ✅ (code + weights) · 146★ · last push 2023-10 (stable)
- **Coverage:** **all 6** + 21 languages, both directions (Indic→Roman model v1.0 released).
- **Install problem:** official `pip install ai4bharat-transliteration` **fails to build on modern Python** (fairseq; verified failure on py3.14 — README of the port says fairseq "crashes on 3.11+").
- **✅ Workaround verified:** `Singla0009/all-indic-transliteration` (HF, **MIT**, CTranslate2 FP32 conversion, ~44 MB/direction, `pip install ctranslate2 huggingface_hub`, supports py3.8–3.13):
  ```python
  import os, ctranslate2
  from huggingface_hub import snapshot_download
  p = snapshot_download("Singla0009/all-indic-transliteration",
                        allow_patterns="indicxlit_indic_en_ct2_fp32/*")
  tr = ctranslate2.Translator(os.path.join(p, "indicxlit_indic_en_ct2_fp32"), device="cpu")
  words = ["नमस्ते", "दुनिया"]
  res = tr.translate_batch([["__hi__"] + list(w) for w in words], beam_size=4)
  print(" ".join("".join(r.hypotheses[0]) for r in res))  # namaste duniya
  ```
- **✅ Real outputs (tested, CPU, beam 4):**
  | Lang | Input | IndicXlit → Roman | Natural target | Notes |
  |---|---|---|---|---|
  | hi | नमस्ते दुनिया कैसे हो | `namaste duniya kaise hoe` | `… kaise ho` | good, "hoe" off |
  | hi | मुझे नहीं पता कि वो कहाँ गया | `mujhe naheen pataa kii voe kahaan gayaa` | `mujhe nahi pata ki wo kahan gaya` | ⚠️ aa/ee conventions |
  | hi | यह एक वीडियो है… | `yeh ack video haye joe youtube parr haye` | `yeh ek video hai jo youtube par hai` | ❌ "ack", "haye" errors |
  | ta | நீங்கள் எப்படி இருக்கிறீர்கள் | `neengkal eppadi irukkireerkal` | `neenga epdi irukkeenga` | ⚠️ dictionary form |
  | te | మీరు ఎలా ఉన్నారు | `meeru elaa unnaaru` | `meeru ela unnaru` | ✅ |
  | ml | സുഖമാണോ | `sukhamano` | `sukhamano` | ✅ |
  | kn | ನೀವು ಹೇಗಿದ್ದೀರಿ | `neevu hegiddiri` | `neevu hegiddeera` | ✅ close |
  | bn | আপনি কেমন আছেন | `aaponi kemon aasen` | `apni kemon achen` | ⚠️ close |
- **Speed:** 11–27 ms per sentence (batched per word) on CPU — negligible for caption cues. ~1 ms/word per the port's claims.
- **Pros:** MIT, offline, tiny (44 MB), 6/6 languages, word-level beam n-best (enables lexicon re-ranking), mature benchmark (SOTA on Dakshina at release: avg 60.6% top-1 vs 51.8% baseline).
- **Cons:** word-level → no sentence context; training-data style leaks through (`naheen`, `pataa`, `kii`); occasional garbage (`ack`); last upstream training ~2022.

### 2.2 LIPI (Hindi Devanagari→Roman, edge CTC)
- **Model:** https://huggingface.co/abhishekgautamm/lipi · code `ABHISHEKgauti25/lipi`
- **License:** **CC-BY-NC-SA 4.0** ⚠️ **non-commercial — disqualified** for our product (source code Apache-2.0, weights/data NC+SA).
- **Coverage:** Hindi only ✗ for 5 other languages.
- **Benchmarks (their table):** beats IndicXlit & ITRANS on typed-Hinglish style (FIRE2013 73.2% vs 34.8%; Dakshina sentences word-match 66.4% vs 33.0%) — strong evidence that *lexicon+attested-spelling training* is what makes output natural. 534K params, CPU-friendly.
- **Verdict:** can't ship weights; **do** steal the methodology (lexicon-first, attested-spelling prior) and its benchmark protocol.

### 2.3 Singla0009/Hindi-to-Hinglish-Pro
- **Model:** https://huggingface.co/Singla0009/Hindi-to-Hinglish-Pro · **License:** MIT ✅ (279 downloads)
- ByT5-small seq2seq, Devanagari→conversational Hinglish, GGUF/ONNX/CT2 formats, explicitly aimed at "video subtitles & captions"; companion "Akshar Engine" (Rust) + RenderCaption desktop app.
- **Coverage:** Hindi only ✗. Untested benchmarks (no published eval I could find).
- **Verdict:** useful **reference implementation for the Hindi polish pass**; not a 6-language solution.

### 2.4 psidharth567/indic-xlit-270M
- Gemma-3-270M finetuned for Indic⇄Latin, 22 languages incl. all 6. License: **no license field (gated Gemma base)** ⚠️ → weights legality unclear. 30 downloads/month. **Verdict:** skip (license risk), but shows the "small instruct model" route is being explored by others.

### 2.5 ASR models that emit Roman directly (upstream alternative)
| Model | License | Coverage | Note |
|---|---|---|---|
| `Oriserve/Whisper-Hindi2Hinglish-{Prime,Apex,Swift}` (repo 30★, HF 165k downloads) | **Apache-2.0** ✅ | Hindi only | Finetuned Whisper that *writes Hinglish directly*; halves WER vs large-v3 (FLEURS 50.8→28.7). Apex = 0.8B. LiveWhisper measured it keeps English words English (better than translate-then-romanize). |
| `abhishekgautamm/whisper-tiny-hinglish` | model card (data NC-ish mix) ⚠️ | Hindi only | 39M, emits `kya scene hai` directly; targets pre-romanized with LIPI. |
| `bodhan-ai/indic-translate` / `spark-ux/indic-transcribe-flex` | **"other"/custom** ⚠️ | 23–27 langs | `mode="romanized"` and "Hindi (Roman)" target; native→Roman CER 8% (IN22-Gen). License too unclear to build on. |

**Implication:** direct-Roman ASR exists but is Hindi-centric today. For a 6-language product, **transcribe native-script → romanize in-app** remains the general path (and is exactly what LiveWhisper's ADR-0002 concluded after 10 failed attempts to make Whisper emit Latin).

---

## 3. Existing OSS projects that already do "Indic → Hinglish captions"

| Project | Repo / license | Stars | What it does | Reuse value |
|---|---|---|---|---|
| **LiveWhisper** | https://github.com/itsskofficial/LiveWhisper · **MIT code, CC-BY-SA 4.0 lexicons** | 1★ (new, 2026-09) | Offline voice-typing for **12 languages** incl. Hinglish/Tanglish(Thanglish)/Banglish/Manglish/Kanglish/Thanglish — i.e., all 6 of ours. **ADR-0002:** tried 10 ways to make Whisper emit Latin → all reverted mid-sentence → decided: *transcribe native, romanize after*. Pipeline: **30k-word Dakshina lexicon (87–92% coverage) → 4.5M-param char model for OOV → letter-by-letter fallback**; user spelling learning; 84.7% words acceptable vs human romanizations; romanize a 156-word sentence in **0.12 ms**. | **Highest.** Its `livewhisper/script/` romanization module, ADRs (0002/0011/0015) and `docs/evaluation.md` are a ready-made architecture + eval harness for Text Magic. Windows app, but the romanizer is separable pure Python. Data files CC-BY-SA (share-alike applies to the lexicons, not the MIT code). |
| **hinglish-reel-captioner** | `SaurabhEffect/HacktoberFest-2026/tree/main/hinglish-reel-captioner` (repo) | small | Offline Hinglish Reels caption studio (Whisper → word captions). Conversion order: **user rules → English loanwords kept English → Hindi lexicon of usual spellings → rule-based transliteration with schwa deletion** (`समझ`→`samajh` not `samajha`; `बनता`→`banta`). Deterministic: a wrong spelling is fixed once. Optional LLM pass with the same discipline. | **High.** The exact fixed-order cascade and schwa rules to copy; editor + timing-preserving respell UX. |
| **hinglish-captions-local** | https://github.com/5ag-70/hinglish-captions-local · **MIT** | 0 | FFmpeg + local Whisper → `romanize_hinglish.py` with editable `hinglish_glossary.json` (`"एप्पल": "Apple"`) + REVIEW-REQUIRED report for approximate spellings; SRT/VTT/TSV out. | Medium — good glossary JSON schema + "flag uncertain words" pattern. |
| **agent-caption** | https://github.com/ahkamboh/agent-caption · **MIT** | small | WhisperX captions, `--hinglish` flag, code-switch transcription (`cs_transcribe.py`), glossary, 15 burn-in styles. | Medium — shows product surface (flags/glossary/style) around the same core. |
| **hinglish-dictation** | https://github.com/3rectangles/hinglish-dictation | small | `hinglish_romanize.py`: Devanagari→Latin deterministic, **English words untouched**; backend shootout (Tara/Apex/Srota) with measured accuracy 6–8/8 for native-script+romanize vs 4/8 and 2/8 for direct-Hinglish ASR. | Medium — evidence that **native-script ASR + post-romanize wins**. |
| **openhinglish** | https://github.com/shankarmishra/openhinglish · **MIT** | small | Deterministic CPU-only **Roman→Devanagari** normalization (opposite direction), 7-stage pipeline, TSV lexicons, traces/n-best, 0.92 EM on 59-sentence bench. Roadmap explicitly plans Telugu/Tamil/Bengali etc. | Medium — lexicon TSV schemas + "structured output with confidence" contract; direction is reverse of ours but its lexicon is a ready-made Roman→native resource we can invert. |
| tanglish-hinglish-nmt, NLP-Hinglish, translation-english-to-hinglish | various | 1–2★ | transliteration+translation pipelines / English→Hinglish via opus-mt | Low. |

**Datasets for lexicons & eval**
- **Dakshina** (Google) — https://github.com/google-research-datasets/dakshina · **CC-BY-SA 4.0** · 12 languages (our 6 included): 25k-word romanization lexicons **with attestation counts** + ~10k human-romanized sentences per language. This is *the* source for the lexicon layer (LiveWhisper derived its 30k-word lexicons from it; CC-BY-SA means derived data files stay SA — code stays MIT, attribute Dakshina).
- **Aksharantar** (AI4Bharat) — HF `AI4Bharat/aksharantar`, ~26M pairs, mixed CC-BY (manual) / CC0 (mined) — safe-ish for building per-language spelling priors.
- `codebyam/Hinglish-Hindi-Transliteration-Dataset` — CC-BY-4.0, 1K–10K reviewed Hinglish↔Devanagari sentence pairs (good few-shot/eval seed for Hindi).

---

## 4. LLM-based romanization (local)

**Prompts from the literature (both verified in papers, both direction Roman→native; invert for ours):**
- *Beyond Specialization* (2505.19851): `system: You are a skilled transliteration expert… Example: "input":["namaste","aadar"],"output":["नमस्ते","आदर"]` + `user: Transliterate the given list… Return only a JSON array.`
- *LREC 2026*: Alpaca-style `Transliterate this romanized {lang} text into native {lang} text.` — LLaMA-3.1-8B zero-shot vs prompt-finetuned vs IndicXlit vs mT5 compared with **CER/WER + BLEUc/BLEUw**.
- *VarDial 2025*: LoRA (r=64) on small open LLMs for Dakshina romanization normalization — the cheapest published path to a fine-tuned local model.

**For our direction (native → colloquial Latin), the practical local options:**
1. **Small instruct model via Ollama/llama.cpp** (e.g., Qwen2.5-3B/7B-Instruct, Gemma-3-4B-IT, Llama-3.1-8B) with a strict caption prompt:
   ```
   Convert ONLY the Indic-script words to natural colloquial {lang} Latin spelling
   (the way people type in chat: क्या→kya, नहीं→nahi). Keep every word already in
   Latin script EXACTLY as it is. Do not translate, do not add words. Output one line.
   Input: यह एक video है जो YouTube पर है
   ```
   Then **validate by diff**: any token that was Latin must be byte-identical; token count must match; otherwise fall back to layer-4 neural output. (Never trust an LLM unguarded — this is the code-switch protection.)
2. **Dedicated seq2seq** (`Singla0009/Hindi-to-Hinglish-Pro`, ByT5) — faster/safer than an LLM but Hindi-only.
3. **Fine-tune** a small model on Dakshina+Aksharantar sentence pairs (native→attested-Roman) — the LIPI/Aksharantar recipe; needed only if LLM polish quality proves insufficient.

**Accuracy evaluation approaches (steal these):**
- **Dakshina human-romanization bench:** WER against *every accepted spelling* + share of words in the *most common* ("usual") spelling + **native-script leak count** (LiveWhisper `tests/bench_romanization.py`, `eval_outputs.py`).
- **Code-switch guard set:** synthetic Hindi/English mixes measuring **% of English words kept English** (LiveWhisper `hinglish_eval.json`; their honest number: 62% kept on hard Marathi mixes — a known weak spot → we must do better via the Latin-passthrough rule, which is trivially ~100% for already-Latin tokens).
- **CER / BLEUc / BLEUw** for sentence-level (LREC 2026 protocol); top-1/top-3 word accuracy (Aksharantar).
- **Per-video consistency test:** same lemma → same spelling across all cues (unique to captions).

---

## 5. Comparison table (the shortlist)

| # | Candidate | Repo | License | ★ / status | Covers our 6? | Install | Speed | Naturalness | Caption verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **Aksharamukha** | virtualvinodh/aksharamukha-python | **AGPL-3.0** ⚠️ | 64★, active | ✅ | `pip install aksharamukha` (py<3.12 bug) | µs | ★★☆ scheme-coloquial | fallback only; license blocker |
| 2 | **sanscript / indic-transliteration** | indic-transliteration/indic_transliteration_py | **MIT** ✅ | 214★, active | ✅ | `pip install indic-transliteration` | µs | ★☆☆ academic | internal reversible IR ✅ |
| 3 | **Indic NLP Library** | anoopkunchukuttan/indic_nlp_library | **MIT** ✅ | 654★, dormant | ✅ | `pip install indic-nlp-library` | µs | ★☆☆ ITRANS | reversible IR alt; dormant |
| 4 | **IndicXlit + CT2 port** | AI4Bharat/IndicXlit (+ `Singla0009/all-indic-transliteration`) | **MIT** ✅ | 146★ / HF port | ✅ (tested) | `pip install ctranslate2 huggingface_hub` | ~15–25 ms/sent | ★★★ typed-ness, needs cleanup | **neural OOV layer ✅** |
| 5 | LIPI | abhishekgautamm/lipi | CC-BY-NC-SA ⛔ | small | Hindi only | torch | fast (CTC) | ★★★★ | methodology only |
| 6 | Hindi-to-Hinglish-Pro | Singla0009/… | MIT ✅ | small | Hindi only | transformers/llama.cpp | fast | ★★★ unverified | Hindi polish reference |
| 7 | Oriserve Whisper-Hindi2Hinglish | OriserveAI/… | Apache-2.0 ✅ | 30★, 165k DLs | Hindi only | transformers | ASR-bound | ★★★★ direct | optional Hindi shortcut |
| 8 | **LiveWhisper romanizer** | itsskofficial/LiveWhisper | MIT + CC-BY-SA data ✅ | 1★ (new) | ✅ 12 langs | repo (pure-python core) | **0.12 ms/156 words** | ★★★★ 84.7% acceptable | **architecture + data to borrow** |
| 9 | hinglish-reel-captioner | SaurabhEffect/… | — | small | Hindi | repo | µs | ★★★★ (rules+lexicon+schwa) | **cascade to copy** |
| 10 | hinglish-captions-local | 5ag-70/… | MIT ✅ | 0★ | Hindi | repo | µs | ★★★ glossary-driven | glossary schema |
| 11 | openhinglish | shankarmishra/… | MIT ✅ | small | Hindi (rev.) | `pip` | µs | n/a (reverse) | lexicon TSV patterns |
| 12 | Generic local LLM (Ollama) | — | model-dependent | — | ✅ all | ollama/llama.cpp | 0.3–2 s | ★★★★ with guard | **batch polish pass only** |

---

## 6. RECOMMENDED PICK — layered architecture

> **Ship a deterministic 4-layer cascade (layers 1–3 always local, µs–ms), with an optional guarded LLM polish pass (layer 4) for final caption text. Build the lexicon from Dakshina; use IndicXlit (MIT, via CTranslate2) only as the OOV fallback; never let an LLM touch a Latin token.**

```
ASR (native script)                ← separate research doc; native-script output is the
        │                             correct default (LiveWhisper ADR-0002, hinglish-dictation bench)
        ▼
L0  TOKEN GATE (code-switch protection)                     [pure rules]
    • Split cue into tokens. Tokens with ZERO Indic codepoints → PASS-THROUGH verbatim
      (never re-transliterated — this alone satisfies "English stays English" for Latin input).
    • Indic-script tokens → candidate queue. Numbers/emoji/URLs pass through.
    • Optional: English-word-in-Devanagari guard (रेडी→"ready") using an English
      pronunciation lexicon + suffix stripping (LiveWhisper ADR-0015 measured only 62% here —
      ship v1 with the simple form: English dictionary hit → keep English).
        ▼
L1  USER GLOSSARY (highest priority)                       [TSV/JSON, persistent]
    • Per-video + global overrides: "doston=dosto", "एप्पल=Apple". One edit fixes every future video
      (pattern from hinglish-captions-local + LiveWhisper ADR-0011).
        ▼
L2  LEXICON LOOKUP (attested colloquial spellings)         [Dakshina-derived, ~30k/lang]
    • native word → list of attested Roman spellings w/ counts; choose highest-count spelling.
    • 83–92% token coverage per language (LiveWhisper measurements) → the majority of caption
      words come out natural by construction, deterministic, ~µs.
        ▼
L3  RULES for the remainder                                [per-language rule pack]
    • Orthographic rules first: Hindi schwa deletion (`समझ`→samajh, `बनता`→banta), anusvara→n,
      post-halant shaping; per-language letter maps (Tamil `ழ`→zh, `ங`→ng …).
    • Then scheme-pivot fallback: IndicXlit CTranslate2 (MIT, 44 MB, ~15–25 ms/cue) with beam n-best,
      re-ranked against the L2 lexicon spelling list (kills `naheen/pataa/ack` outputs).
    • Deterministic per word → per-video consistency guaranteed.
        ▼
L4  (OPTIONAL, batch/offline) LLM POLISH PASS              [Ollama, small instruct model]
    • Sentence-level only — context helps (`है`→hai, `सर`→sir vs sar) where word-level can't.
    • Prompt: rewrite ONLY Indic-derived tokens into chat-natural spelling; keep Latin tokens
      byte-identical; no translation, no added/dropped words.
    • Hard guard: diff validation — Latin tokens unchanged ∧ token count preserved ∧ no new
      Indic codepoints ∧ output re-enters L0 check; violations → keep L3 output.
    • Never in the interactive/real-time path (0.3–2 s/cue); only on export.
```

**Why this shape (evidence):**
- **Naturalness** comes from *attested spellings* (lexicon) — LIPI's benchmark shows lexicon/attested prior beats pure schemes by 2× on typed-Hinglish data; LiveWhisper hits 84.7% acceptable with lexicon-first.
- **Lossless schemes (ISO/ITRANS/HK/Velthuis) stay internal** — RomanSetu proved lossy-but-natural wins for human-facing tasks; sanscript/IndicNLP give us the reversible pivot for free (MIT).
- **Word-level for the hot path, sentence-level only in the guarded LLM pass** — captions need per-word timing/consistency; LREC 2026 shows sentence context matters, which L4 captures without sacrificing determinism (its output is cached per cue).
- **Code-switching** is solved structurally at L0 (Latin passthrough) rather than by hoping a model behaves (LiveWhisper's Hinglish-Prime kept only 62% of English on hard mixes; Oriserve keeps English natively but Hindi-only).
- **License-clean:** MIT everywhere (sanscript, IndicNLP, IndicXlit+CT2 port, LiveWhisper code, caption projects, OpenHinglish), Apache-2.0 for any Oriserve shortcut; **excluded:** Aksharamukha/indic-trans (AGPL), LIPI weights (NC), Gemma-finetune (unset license), bodhan/spark-ux models ("other").
- **Offline:** heaviest asset is 44 MB (IndicXlit CT2) + ~30 MB of lexicons; no GPU needed; LLM layer optional and user-supplied via Ollama.

**Build order:** L0+L1+L2+L3(scheme only) first (a weekend of work, already natural for 80–90% of tokens) → add IndicXlit CT2 for OOV → per-language rule packs (start Hindi + Tamil) → eval harness on Dakshina sentences → L4 polish last.

**Named components to vendor/borrow:**
1. `indic-transliteration` (MIT) — scheme pivot & round-trip checks.
2. `Singla0009/all-indic-transliteration` (MIT, CT2) — IndicXlit neural fallback without fairseq.
3. Dakshina lexicons (CC-BY-SA, attribute) — L2 spelling tables.
4. LiveWhisper `livewhisper/script/` + `docs/evaluation.md` (MIT/CC-BY-SA) — romanizer cascade & metrics to port.
5. hinglish-reel-captioner rule order + schwa rules — L3 rule pack seed.
6. Ollama + Qwen2.5/Gemma-3 small instruct — L4, guarded.
