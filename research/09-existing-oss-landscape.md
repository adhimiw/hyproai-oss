# 09 — Existing Open-Source Landscape for a HyproAI Clone

**Date:** 2026-11-04 · **Method:** GitHub API metadata pulls (stars / license / last push verified programmatically today), GitHub topic + search API sweeps, web search (Reddit, HN, blogs, awesome lists), Hugging Face Spaces API, and a live read of hyproai.com.

**Target product (from hyproai.com, verified today):** browser-based AI video editor for Indian-language creators.

HyproAI's actual shipped feature set (our pipeline stages):

| # | Stage | HyproAI branding |
|---|-------|-------------------|
| S1 | Transcription (word-level, 6+ Indic langs, code-switching, slang) | "AI Transcription" |
| S2 | Romanization / transliteration (Tamil→Thanglish, Hindi→Hinglish, Telugu→Telugish, Kannada→Kanglish, Malayalam→Malayalish, Bengali→Benglish) + translate | "Text Magic" |
| S3 | Silence / dead-air removal ("uh, um, wait let me think") | "Silence Remover" |
| S4 | Retake removal (detect repeated sentences, keep cleanest take) | "Retake Remover" |
| S5 | Animated captions — 12 viral styles, word-level timing, slide-up/glow/word-by-word | "Caption Styles / Caption animations" |
| S6 | Audio cleanup (noise reduction, mic→studio) | "Audio cleanup" |
| S7 | NLE export (Premiere / DaVinci / After Effects, 5 separate layers) + SRT/VTT/TXT | "Export to editors" |
| S8 | Browser/timeline/cloud editor UI | "Cloud editing" |

---

## 1. End-to-end AI video / podcast editors

| Project | Repo | License | Stars / last push | What it does | Stages covered | Stack | Verdict |
|---|---|---|---|---|---|---|---|
| **MoneyPrinterTurbo** | github.com/harry0703/MoneyPrinterTurbo | MIT | **138,398** / 2026-11-04 | Topic → LLM script → stock footage → TTS → subtitles → MP4. REST API + CLI. | S5 (basic burn), partial S1 (faster-whisper `large-v3-turbo`) | Python, faster-whisper, moviepy/ffmpeg | **Steal:** subtitle config surface (font/position/color/outline/background), job-orchestration pattern. Not an editor — no timeline, no cutting. |
| **video-use** | github.com/browser-use/video-use | MIT | **30,505** / 2026-10-08 | Agent-driven video editing CLI: `transcribe`, `pack --silence-threshold`, `timeline`, `render edl.json --build-subtitles`, `grade`. Explicit **multi-take "pick best take of each beat"** sub-agent brief. | S1, S3, S4 (agent-level), S5, S7 (EDL) | Python | **REUSE heavily** — closest existing thing to our whole pipeline; MIT. Read its EDL schema, silence packing, and take-selection prompt. |
| **pyvideotrans** | github.com/jianchang512/pyvideotrans | GPL-3.0 | 19,631 / 2026-11-03 | Video translation/dubbing: STT → SRT → translation → TTS → subtitle embed. | S1, S7 (SRT), translation | Python | Steal subtitle timing/translation plumbing **only as reference** — GPL infects our code. |
| **OpenCreator** (fka KrillinAI) | github.com/krillinai/OpenCreator | Apache-2.0 | 14,140 / 2026-11-03 | Video translation & dubbing for 120+ languages; downloader + transcribe + translate + TTS. | S1, S7, translation | Python/TS | Steal translation-pipeline reference (Apache-safe). |
| **AutoClip** | github.com/zhouxiaoka/autoclip | MIT | 9,963 / 2026-11-04 | Desktop long→shorts: clips + subtitles + covers + copy for Douyin/XHS/Shorts. | S1, S5, clipping | Python | Steal caption/clip packaging patterns. |
| **ShortGPT** | github.com/RayVentura/ShortGPT | MIT | 8,104 / **2025-02-10 (stale)** | Shorts automation: footage sourcing, voiceover, editing. | none of ours meaningfully | Python, ffmpeg | **Ignore** — stale, script-to-video not edit-what-you-shot. |
| **OpenShots** | github.com/mutonby/openshorts | MIT | 7,462 / 2026-11-02 | Long → viral 9:16: AI moment detection, face tracking, subtitles, dubbing; Docker self-host. | S1, S5, auto-reframe | Python | **Steal:** auto-reframe (face tracking) + moment detection. |
| **AI-Youtube-Shorts-Generator** | github.com/Anil-matcha/AI-Youtube-Shorts-Generator | MIT | 5,752 / 2026-11-03 | LLM highlight detection (with local Ollama path) → Whisper → vertical crop → captioned clips; emits `highlights.json` (start/end/score/hook/reason). | S1, S5, clipping | Python | **Steal:** highlight-ranking prompt + JSON schema + `local/clipper.py` ffmpeg crop recipe. |
| **OpenChatCut** | github.com/0xsline/OpenChatCut | **AGPL-3.0** | 2,895 / 2026-10-21 | Local-first *conversational* editor: multi-track timeline, Agent Skills, MCP, Remotion rendering. | S8, partial S1/S5 | TS/React | Architecture reference only — **AGPL, do not vendor code**. |
| **VoiceStudio** | github.com/debpalash/VoiceStudio | **AGPL-3.0** | 100,250 / 2026-11-04 | Local ElevenLabs alt: transcription, dubbing, voice cloning, 646 languages. | S1, S6 (voice), S7 | Python | AGPL — model/pipeline reference only. |
| **OpenCut** | github.com/OpenCut-app/OpenCut | MIT | **97,270** / 2026-09-24 | The open-source CapCut alternative — timeline UI, manual editing. (AI lives in forks.) | S8 | React/TS | **Steal timeline/UI patterns** (MIT). |
| **OpenCut-AI** | github.com/randyaswin/OpenCut-AI | MIT | 2 / 2026-06-28 | Fork adding Smart Cut filler removal, edit-by-text, voice clone. | S3, S1 | TS | Read for how they bolted AI onto OpenCut; too small to depend on. |
| **Auto Video Editor** | github.com/benpiper/auto-video-editor | MIT | 12 / 2026-09-17 | Removes silence + filler words ("um/uh") + static/freeze moments. | S3 (+filler) | Python, pydub | **Steal:** filler-word + silence heuristics. |
| **ghost-editor** | github.com/kurbaitaev/ghost-editor | MIT | 195 / 2026-10-30 | "Raw talking-head take in, finished reel out" for Claude Code. | S3, S4 (partial), S7 | Python | Steal take-assembly logic. |
| **buttercut** | github.com/barefootford/buttercut | NOASSERTION | 616 / 2026-10-23 | Edit video with Claude Code. | S8 | Ruby | Reference only; license unclear. |
| **OpenCreator/others (clippers)** | podcli (AGPL, 81), reelforge (MIT, 10), cutawan (MIT, 37), clipmint (MIT, 27), ai-video-clipper (MIT, 19), shorts-generator (MIT, 7) | mixed | small / all Nov 2026 | OpusClip-style vertical clip generators: whisper → face-track → burn captions. | S1, S5, S7 | Python/TS | Mine for caption-burn + reframe recipes; all tiny. |
| **MarioRodrigues10/shorts** | github.com/MarioRodrigues10/shorts | MIT | 4 / 2026-11-03 | Claims *exactly* our feature list: "Auto captions, edit by transcript, **silence and retake removal**, social-ready export, 100% local." | S1, S3, S4, S5, S8 | JS | Proof the feature combo is buildable; too immature to depend on — **read its retake logic**. |
| **hypit** | github.com/hypit-ai/hypit | NOASSERTION | 27,817 / 2026-11-03 | "Clone any viral video with AI agents" — face/word/B-roll swap, 100 variants. | off-axis | TS | Ignore for v1. |
| **EverythingAI-Pro/ai-video-editor** | github.com/EverythingAI-Pro/ai-video-editor | NOASSERTION | 25 / 2026-06-21 | Auto-cut filler, auto-place B-roll, auto-caption, long→shorts. | S3, S5 | Python | Reference only. |
| **MoneyPrinterTurbo-adjacent: hyperedit** | github.com/kevinbadi/hyperedit | NONE | 223 / 2026-09-21 | AI editor with FFMPEG + Remotion + Obsidian agents. | S8 | TS | Reference only, no license. |

**Honest read on category 1:** every "end-to-end" project is either (a) *script-to-video* (MoneyPrinterTurbo, ShortGPT — they don't edit your footage), (b) *long-to-shorts clipper* (openshorts, autoclip, AI-Youtube-Shorts-Generator — different job), or (c) *agent wrappers* (video-use, OpenChatCut). **Nobody ships a Descript-style, transcript-driven, Indic-language editor as a product.** video-use is the strongest reusable foundation for S1/S3/S4/S7 logic.

---

## 2. Browser / web editor cores usable as UI or engine

| Project | Repo | License | Stars / last push | What it does | Verdict |
|---|---|---|---|---|---|
| **Diffusion Studio Core** | github.com/diffusionstudio/core | **MPL-2.0** | 1,252 / 2025-11-18 | Browser video *compositing engine* on **WebCodecs** — `Composition`, timeline, clips, effects, export. The rendering heart of a web NLE. | **REUSE as-is** — MPL-2.0 is file-level copyleft, safe for a commercial OSS product if modifications to its files stay MPL. |
| **Diffusion Studio Editor** | github.com/diffusionstudio/editor | MPL-2.0 | 3,464 / 2026-10-18 | Full web editor ("VS Code of video editing, built for agents"): inspect footage, cut clips, add captions, render. | **REUSE / fork patterns** — closest complete web NLE. |
| **Mediabunny** | github.com/Vanilagy/mediabunny | **MPL-2.0** | 7,428 / **2026-11-03** | Zero-dependency pure-TS media toolkit: read/write/convert MP4/WebM/MP3/HLS via WebCodecs — "ffmpeg for the web". | **REUSE as-is** — demux/mux/transcode/trim in-browser without shipping ffmpeg.wasm. Actively maintained. |
| **ffmpeg.wasm** | github.com/ffmpegwasm/ffmpeg.wasm | MIT | 17,950 / 2026-09-29 | Full ffmpeg compiled to WASM. | Fallback only — slow on multi-GB files; keep for exotic filters (libass burn, loudnorm). |
| **OmniClip** | github.com/omni-media/omniclip | **MIT** | 1,469 / 2026-10-31 | Browser editor as **web components** (`<omni-timeline>`, `<omni-media>`, `<omni-text>`), WebCodecs render up to 4K, npm-installable. | **REUSE timeline components as-is (MIT).** |
| **OpenReel Video** | github.com/Augani/openreel-video | MIT | 5,574 / 2026-10-17 | Browser CapCut alternative with an AI agent *inside* the timeline (plan → review → model edits). | Steal agent-timeline interaction patterns. |
| **OpenVideo Editor** (fka designcombo/react-video-editor) | github.com/openvideodev/react-video-editor | **NOASSERTION** | 1,829 / 2026-06-30 | React + **Remotion** CapCut/Canva clone: full multi-track timeline, layers, drag-drop. | Steal UI structure; **license unclear + Remotion dependency** — do not vendor code verbatim. |
| **Donkey** | github.com/DonkeyCut/Donkey | Apache-2.0 | 334 / 2026-10-30 | Local-first browser editor: multi-track timeline, captions, AI assistant. | Apache-safe pattern reference. |
| **Revideo** | github.com/midrender/revideo | MIT | 4,174 / 2026-07-15 | Motion Canvas fork — programmatic video with code (render MP4 headless). | Use only if we go server-render; not an editor UI. |
| **HyperFrames** | github.com/heygen-com/hyperframes | **Apache-2.0** | **78,478** / 2026-11-04 | "Write HTML, render video." Deterministic HTML/CSS/animation → MP4; ships agent skills/plugins. | **REUSE for S5 caption rendering + S7 export**: author caption styles as HTML/CSS, render deterministically. |
| **Etro** | github.com/etro-js/etro | **GPL-3.0** | 1,154 / 2026-10-24 | Canvas-based programmatic video editing library (layers, effects). | **Ignore** — GPL + superseded by WebCodecs engines. |
| **Remotion** | github.com/remotion-dev/remotion | **"Remotion License" (NOT OSI OSS)** | 70,507 / 2026-11-03 | React → video rendering. Free only for individuals & companies ≤3 employees; paid otherwise. | **Do not build core on it.** Fine for demos/preview; a commercial OSS clone must avoid it for export. |
| **Vanta** | github.com/itsjwill/vanta | NOASSERTION | 160 / 2026-07-26 | Remotion AI video engine: animated captions, avatars, voice clone. | Steal caption preset ideas only. |
| **MoviePy** | github.com/Zulko/moviepy | MIT | 15,107 / 2026-08-26 | Python video editing (clips, effects, text overlay). | Backend convenience for server-side burn/concat; slower than ffmpeg filter graphs. |
| **OpenTimelineIO** | github.com/AcademySoftwareFoundation/OpenTimelineIO | Apache-2.0 | 2,055 / 2026-10-31 | Editorial timeline interchange format (C++/Python). | **REUSE as internal timeline representation** (see S7). |
| **ChatOctopus timeline** | github.com/ChatOctopus/timeline | MIT | 8 / 2026-06-24 | **Import/export FCPXML 1.8, Premiere xmeml v5, DaVinci, OTIO** — read *and* write. | **REUSE as-is for S7** — exactly the "5 layers into Premiere/Resolve/Ae" feature. Tiny but MIT and purpose-built. |

**Desktop NLEs (for S7 export / headless rendering, not UI):**

| Project | Repo | License | Stars / last push | Notes |
|---|---|---|---|---|
| Shotcut | github.com/mltframework/shotcut | GPL-3.0 | 15,591 / 2026-11-04 | MLT-based; `melt` CLI can render from MLT XML headlessly. |
| MLT framework | mltframework.org (+ `melt`) | LGPL/GPL | — | **Programmatic editing**: XML timeline → headless render. Kdenlive/Shotcut both sit on it. Kdenlive project files are MLT XML. |
| OpenShot | github.com/OpenShot/openshot-qt | NOASSERTION | 6,741 / 2026-11-04 | Python; has a Python API (`openshot` bindings) — usable for automation. |
| Olive | github.com/olive-editor/olive | GPL-3.0 | 9,148 / **2024-12-05 (stale)** | Half-built NLE. Ignore. |
| LosslessCut | github.com/mifi/lossless-cut | GPL-2.0 | 45,680 / 2026-11-03 | Stream-copy lossless trimming. Reference for fast remux-based cutting. |
| CLI-Anything (kdenlive skill) | github.com/HKUDS/CLI-anything | Apache-2.0 | 58,197 / 2026-09-22 | Ships a Kdenlive skill: JSON project → MLT XML generation. Pattern for "JSON timeline → NLE file". |

**Verdict on category 2:** For a browser product, the winning combination is **`@diffusionstudio/core` (MPL) + Mediabunny (MPL) for media IO + OmniClip/OpenCut for timeline UI patterns + HyperFrames (Apache-2.0) or libass for deterministic caption rendering**. Do not use Remotion (license), do not use Etro (GPL/legacy), do not write an ffmpeg.wasm-based editor from scratch.

---

## 3. Text-based / transcript editors (Descript-class, OSS)

| Project | Repo | License | Stars / last push | What it does | Stages | Verdict |
|---|---|---|---|---|---|---|
| **CutScript** | github.com/DataAnts-AI/CutScript | **MIT** | 270 / 2026-03-06 | Local-first Descript clone: **WhisperX word-level transcription**, edit-video-by-editing-text, filler-word detection, Electron + React/Vite/Tailwind/Zustand + FastAPI backend. | S1, S3/S4 (filler), S8 | **REUSE architecture + steal algorithms (MIT).** Best direct reference for transcript↔timeline sync (`useVideoSync`, transcript editor component, project schema in `shared/`). |
| **Audapolis** | github.com/bugbakery/audapolis | **AGPL-3.0** | 1,905 / 2026-06-24 | Editor for spoken-word audio with automatic transcription — paragraph-level transcript editing over audio. | S1, S8 (audio) | Study the transcript-editing UX/model; **AGPL: do not copy code** into an MIT/commercial product. |
| **OpenChatCut** | github.com/0xsline/OpenChatCut | AGPL-3.0 | 2,895 / 2026-10-21 | Conversational editing over a real multi-track timeline + MCP + agent skills. | S8 | Reference only (AGPL). |
| **YusafCut** | (no repo found; seen on YouTube as "Rust + TypeScript native macOS text-based editor") | — | — | Claimed OSS text-based editor. | — | **Unverified** — no GitHub repo located; treat as non-existent until found. |
| **hyperedit / "HYPER EDIT V3"** | github.com/kevinbadi/hyperedit | NONE | 223 / 2026-09-21 | "AI-powered video editor with FFMPEG, Remotion & Obsidian agents." | S8 | No license → can't reuse code. |
| **DeltaCut** | github.com/deltaqyto/DeltaCut | GPL-3.0 | 0 / 2026-01-07 | "Non-linear transcript video editor." | S8 | Too small. |
| **ExpressiveCaptions (ExpCaptionsbeta)** | github.com/robbolivercreates/ExpCaptionsbeta | NONE | 0 / 2026-07-06 | Descript-style transcript editor + expressive burned captions (Whisper timing + tone). | S1, S5, S8 | Idea reference only. |
| **lossless-cut issue #1784** | github.com/mifi/lossless-cut | — | — | Open feature request: "text-based editing via subtitle" — **the mainstream tools still haven't built it.** | — | Confirms whitespace in this niche. |

**Verdict on category 3:** **CutScript (MIT) is the single best codebase to study** for transcript-driven editing; Audapolis for UX. Nothing here is Indic-aware, none do retakes, none export to Premiere.

---

## 4. Caption-specific generators (viral/shorts style presets)

| Project | Repo | License | Stars / last push | What it does | Verdict |
|---|---|---|---|---|---|
| **ai-video-captions** | github.com/nicolaigaina/ai-video-captions | **MIT** | 70 / 2026-03-27 | **6 viral presets — Hormozi, MrBeast, Karaoke, Minimal, Bounce, Classic**; word-level animation (highlight/wipe/bounce/scale); 100+ languages; Docker web app. | **REUSE preset definitions & animation logic (MIT)** — directly maps to HyproAI's "12 styles". |
| **pycaps** | github.com/francozanardi/pycaps | **MIT** | 230 / 2026-09-30 | Animated subtitles via **Python + CSS templates**: `pycaps render --template minimalist --transcript transcript.json`. Template = HTML/CSS. | **REUSE** — authoring model (CSS-based style presets + word JSON) is exactly right for S5. |
| **KillerSubtitles** | github.com/glenwrhodes/KillerSubtitles | MIT | 5 / 2026-07-21 | Whisper word-level → karaoke-style line build-up burn. | Steal the word-grouping + highlight algorithm. |
| **Beautiful-Captions** | github.com/AayushGupta16/Beautiful-Captions | MIT | 10 / 2026-05-08 | Word-level SRT styling: auto font scaling, max words/line, censoring. | Steal layout heuristics (auto-scale, line breaking). |
| **subsai** | github.com/absadiki/subsai | **GPL-3.0** | 1,680 / 2026-04-20 | Web-UI + CLI + Python package for subtitle generation (Whisper variants). | Reference only (GPL). |
| **Subtitle Edit** | github.com/SubtitleEdit/subtitleedit | MIT | 14,844 / 2026-11-03 | The reference subtitle editor: every format, timing, OCR, sync tool. | **Steal** format/normalization logic & timing utilities (MIT, C#). |
| **libass** | github.com/libass/libass | ISC | 1,191 / 2026-10-30 | The ASS/SSA subtitle renderer used by ffmpeg's `ass=` filter — karaoke (`\kf`, `\k`) built in. | **REUSE as burn engine** (ISC, battle-tested, supports complex animation). |
| **Aegisub** | github.com/Aegisub/Aegisub | NOASSERTION | 3,379 / 2025-01-01 | Advanced ASS subtitle authoring. | Reference for what "style presets" should contain. |
| **Vanta / MoneyPrinterTurbo / hyperframes** | see above | — | — | MTB: font/size/position/color/outline/background per subtitle. HyperFrames: HTML caption blocks → MP4. | Steal config surface + HTML caption rendering path. |
| **Whisper→ASS karaoke recipe** (Rendobar blog, code sample) | — | blog code | 2026 | ~40 lines: Whisper word JSON → ASS with `\kf` per word → `ffmpeg -vf ass=captions.ass`. | **Steal the algorithm** (it's a snippet, not a project). This is the cheapest correct S5 pipeline. |

**HF Spaces check:** `search=captions` and `search=silence remover` Spaces are toys (max 10 likes: `raydesoky/silence-remover-made-by-rx66y`, `innoai/ai-video-editor` 27 likes). **Nothing production-grade on HF** — no risk of being scooped there, and no reusable Space.

---

## 5. Silence removal & retake removal

| Project | Repo | License | Stars / last push | What it does | Verdict |
|---|---|---|---|---|---|
| **auto-editor** | github.com/WyattBlue/auto-editor | **Unlicense** (public domain) | 5,758 / 2026-11-02 | The standard tool for "effortless video editing": detects silent ranges and cuts them. Written in **Nim**. | **Steal the algorithm/threshold model** (public domain, no attribution burden). Best-in-class heuristics. |
| **jumpcutter** | github.com/carykh/jumpcutter | MIT | 3,144 / 2024-02-28 (stale) | Classic silence remover: `--sounded_speed --silent_speed --frame_margin --frame_rate` over ffmpeg. | Steal parameter semantics (frame_margin, threshold) — MIT. |
| **silencecut-ffmpeg** | github.com/beenotung/silencecut-ffmpeg | BSD-2-Clause | 14 / 2025-04-30 | Node/TS: `silencedetect` → invert → cut. `silentDetectAndRemove({durationThreshold, noiseLevelThreshold})`. | Reuse directly if our backend is TS; BSD. |
| **auto-video-editor** | github.com/benpiper/auto-video-editor | MIT | 12 / 2026-09-17 | Silence + filler words + freeze-frame removal via pydub. | Steal filler-word list + segmentation logic. |
| **ffmpeg `silencedetect` + `select`/`aselect` recipe** | ffmpeg docs/blogs | — | — | Detect → invert → cut both streams at identical timestamps (`setpts`/`asetpts`). `silenceremove` alone desyncs lip-sync. | Baseline S3 implementation (this is the known-correct approach). |
| **RemoveRetakes** | github.com/p-o-f/RemoveRetakes | GPL-3.0 | **0** / 2026-03-30 | "Removes retakes for mp3 recordings." | Proof of concept only. |
| **ScriptCutAI** | github.com/omeravcioglu/ScriptCutAI | NOASSERTION | **0** / 2026-09-26 | Local Whisper + script alignment → **removes retakes, mistakes, off-script talking** → Premiere cuts. | Closest published description of S4; too small to reuse, but **read its alignment approach**. |
| **voice-take-editor** | github.com/phillipjohnsen-ai/voice-take-editor | NONE | 1 / 2026-07-03 | Groups same sentence across takes, ranks by quality, "pick best take per sentence". | Concept reference (UI + grouping model). |
| **video-use multi-take brief** | github.com/browser-use/video-use | MIT | 30,505 | Ships an explicit prompt/JSON contract: phrase-level transcripts of all takes → ranked best take per beat → EDL, with rules ("word boundaries only", "prefer silences ≥400ms as cut targets"). | **Steal the prompt + rules + EDL schema (MIT).** |
| **ghost-editor** | github.com/kurbaitaev/ghost-editor | MIT | 195 / 2026-10-30 | Raw talking-head take → finished reel (multi-take assembly). | Steal assembly logic. |
| **Openklip** (from search results) | github.com/craftled/openklip | — | not found via API | "Multi-take assembly: `take-add`/`takes`/`assemble` splice best take per line." | Verify repo path before citing; concept matches S4. |

**GAP (important):** Silence removal is a solved, reusable problem. **Retake removal is NOT — the entire OSS field is 0–200 star scripts.** No project does *sentence-level repeated-take detection with best-take scoring* at production quality, and **none do it for Indic speech**. This is our sharpest differentiator and must be built in-house.

---

## 6. Transcription, Indic languages & romanization

| Project | Repo | License | Stars / last push | Role | Verdict |
|---|---|---|---|---|---|
| **Whisper** | github.com/openai/whisper | MIT | 111,908 / 2026-08-31 | ASR baseline (multilingual incl. Indic, but weak on code-switching/slang). | Reuse (via faster-whisper). |
| **faster-whisper** | github.com/SYSTRAN/faster-whisper | **MIT** | 26,461 / 2026-11-02 | CTranslate2 Whisper — 4× faster, GPU/CPU, word timestamps. Used by MoneyPrinterTurbo. | **REUSE as our S1 engine (primary choice).** |
| **whisper.cpp** | github.com/ggml-org/whisper.cpp | MIT | 54,902 / 2026-11-03 | C/C++ Whisper — best for client-side/local or edge. | Reuse if we need on-device. |
| **WhisperX** | github.com/m-bain/whisperX | **BSD-2-Clause** | 25,080 / 2026-10-22 | **Word-level timestamps** (wav2vec2 forced alignment) + diarization. | **REUSE for word alignment (S1→S5 timing).** CutScript uses exactly this. |
| **Indic-Subtitler** | github.com/kurianbenoy/Indic-Subtitler | MIT | 95 / 2026-10-25 | Transcribes + translates Indic-language video/audio (Jupyter/notebook-driven, live at indicsubtitler.in). | Steal Indic language-code mapping + pipeline; small. |
| **IndicXlit (Aksharantar)** | github.com/AI4Bharat/IndicXlit | MIT | 146 / **2023-10-13 (stale)** | Neural transliteration models for **21 Indic languages** (script↔Latin, and Latin→Indic). | **Reuse weights/checkpoint as S2 seed** — exactly the romanization problem. Stale repo but model is the asset. |
| **indic_transliteration_py** | github.com/indic-transliteration/indic_transliteration_py | MIT | 214 / 2026-10-16 (active) | `sanscript.transliterate()` — deterministic script↔script (Devanagari↔Tamil↔Latin schemes). | **REUSE for script normalization / fallback** (not natural "Hinglish", but needed for pipeline hygiene). |
| **IndicTrans2** | github.com/AI4Bharat/IndicTrans2 | MIT | 490 / 2025-10-03 | Translation models for **22 scheduled languages of India**. | Reuse for S2 "translate" (native script ↔ English) — MIT. |
| **AI4Bharat IndicWhisper / transliteration pages** | ai4bharat.iitm.ac.in | — | — | Indic ASR + transliteration model family (Xlit, ASR, MT, TTS). | Model source to evaluate; check each checkpoint's license. |
| **VoiceStudio** | github.com/debpalash/VoiceStudio | AGPL-3.0 | 100,250 / 2026-11-04 | Local transcription/dubbing in 646 languages. | AGPL — reference only. |
| **pyvideotrans / OpenCreator** | above | GPL / Apache | — | SRT translation + TTS dubbing flows for Indian languages. | Apache one (OpenCreator) is the safer reference. |

**GAP:** Whisper's Indic accuracy on *colloquial, code-switched* speech (Tanglish/Telugish speech mixed with English words) is the hard part — no OSS project solves "Thanglish captions with slang handling." HyproAI's FAQ explicitly claims "word-level timestamps with handling for colloquial slang, regional accents, and code-switching." **This needs our own data/finetune/LLM-correction layer on top of faster-whisper + WhisperX.**

---

## 7. Audio cleanup (S6)

| Project | Repo | License | Stars / last push | Notes | Verdict |
|---|---|---|---|---|---|
| **DeepFilterNet** | github.com/Rikorose/DeepFilterNet | **"Other" (NOASSERTION — verify LICENSE file)** | 4,951 / **2024-10-17 (stale)** | Deep-filtering speech noise suppression; real-time capable; ONNX export. | Strongest quality option; **confirm license text before use**, consider forking. |
| **RNNoise** | github.com/xiph/rnnoise | **BSD-3-Clause** | 5,934 / 2025-02-22 | Lightweight NN noise suppression (WebRTC-grade), C, trivially WASM-able. | **REUSE as in-browser cleanup (BSD, safe).** |
| **demucs** | github.com/facebookresearch/demucs | MIT | 10,403 / **archived 2024-04** | Source separation (vocals/drums/bass/other). | Archived — could isolate vocals for better ASR/cleanup, but maintenance risk. |
| **ffmpeg filters** (`afftdn`, `loudnorm`, `highpass`, `speechnoise`) | ffmpeg | — | — | Zero-dependency noise gate + EBU R128 loudness normalization. | **Baseline S6 for free**; layer neural denoise on top. |
| **Ultimate Vocal Remover** (not pulled) | github.com/Anjok07/ultimatevocalremovergui | GPL-3.0 | large | Vocal isolation GUI. | Reference only (GPL). |

---

## 8. NLE export (S7) — "5 layers into Premiere / Resolve / After Effects"

| Project | Repo | License | Stars | Capability |
|---|---|---|---|---|
| **ChatOctopus/timeline** | github.com/ChatOctopus/timeline | MIT | 8 | `exportTimeline(timeline, "fcpx" \| "premiere" \| "resolve" \| "otio")` — FCPXML 1.8 **and** xmeml v5 read+write. Exactly our export feature. |
| **OpenTimelineIO** | github.com/AcademySoftwareFoundation/OpenTimelineIO | Apache-2.0 | 2,055 | Industry interchange; can round-trip to most NLEs via adapters. |
| **MLT XML + `melt`** | mltframework | LGPL/GPL | — | Kdenlive/Shotcut project format; headless render; CLI-Anything ships a JSON→MLT skill (Apache-2.0). |
| **Premiere Pro MCP skills** | hetpatel-11/Adobe_Premiere_Pro_MCP, leancoderkavy/premiere-pro-mcp, ayushozha/AdobePremiereProMCP | mixed | small | Drive a *live* Premiere to import/arrange — an alternative to file export (user must have Premiere open). |
| **Remotion issue #10235** | — | — | — | Remotion still has **no** EDL/FCPXML/OTIO export (open feature request as of 2026-08) → another reason not to build on Remotion. |

**Verdict:** Build our own internal timeline model (JSON), then **emit via ChatOctopus/timeline (MIT) + OTIO (Apache-2.0)**. This is a thin, writable layer — low risk.

---

## 9. Awesome lists & indexes worth mining

| List | Repo | License / stars | Value for us |
|---|---|---|---|
| **Awesome Claude Video Skills** | github.com/zhuyansen/awesome-claude-video-skills | "other" / 1,529 | **Highest-yield index found:** 180 agent-video repos, security-graded, with dedicated **✂️ Editing** and **📱 Shorts & social** sections. Fresh (2026-11-04). |
| **awesome-video-production** | github.com/ad-si/awesome-video-production | MIT / 73 | GUI editors, **CLI/Code** section (jumpcutter, moviepy, revideo, video2X), SaaS. Clean taxonomy. |
| **awesome-video** | github.com/sitkevij/awesome-video | CC0 / 436 | Frameworks/libraries/specs (WebCodecs, container formats). |
| **awesome-video-editing** | github.com/wentianli/awesome-video-editing | none / 102 | **Academic paper list** — text-based editing, multi-take editing, highlight detection. Useful for S4 research (e.g. "Learning to Cut by Watching Movies"). |
| **Awesome-Video-Diffusion** | github.com/showlab/Awesome-Video-Diffusion | awesome / large | Research-side; off-axis for us. |
| **awesome-ai-shorts** | github.com/nicolaigaina/awesome-ai-shorts | — / — | Directories of shorts tools incl. text-based/filler-removal competitors. |
| **awesome-ai-tools (given)** | github.com/mahseema/awesome-ai-tools | MIT / 6,539 (last 2025-12-31) | **Low value — checked directly:** the "Generative AI Video" and "Generative AI Audio" sections are ~entirely **SaaS marketing links** (Veed, Descript, CapCut, Adobe…). The only GitHub items are TTS: `suno-ai/bark`, `neonbjb/tortoise-tts`, `rsxdalv/tts-generation-webui`. **Nothing reusable for our pipeline.** Do not spend more time here. |
| **awesome-selfhosted** | github.com/awesome-selfhosted/awesome-selfhosted | other / 333k | "Video Editing / Media Streaming" sections — good for deployment/ops references. |
| **rShetty/awesome-podcasts** | — | none / 13,151 | Podcast *directories*, not tools. Ignore. |
| **Pixo "Open-Source AI Video: 50+ Pipelines"** (blog, pixo.video) | secondary index | — | Cross-references video-use, video-podcast-maker, buttercut. Useful sanity check. |
| **ai-video-reuse-tools** | github.com/ggg9346/ai-video-reuse-tools | — / — | Competitor feature matrix (Descript/OpusClip filler-removal features) — useful for feature parity checklist, not code. |

---

## 10. Combined "silence + captions + Indic" search result

Searched: `silence captions video`, `retake video editing`, `remove retakes`, `indic subtitles`, `thanglish hinglish captions`, HF Spaces, topics `ai-video-editor` / `auto-captions` / `text-based-video-editor`.

**Finding: zero projects combine all four.** Closest partials:

- `MarioRodrigues10/shorts` (MIT, 4★) — claims silence + retake + transcript editing + captions, but no Indic, tiny codebase.
- `EverythingAI-Pro/ai-video-editor` (25★) — filler + captions, no Indic, no retakes.
- `Indic-Subtitler` (MIT, 95★) — Indic transcription/translation only, no editing.
- `ScriptCutAI` (0★) — retakes via script alignment, Premiere-only, no captions/Indic.
- HyproAI itself is the only product shipping the combination — and it's closed SaaS.

**Whitespace confirmed: no open-source HyproAI equivalent exists.**

---

## 11. Reuse verdict summary (license safety first)

**Safe to vendor (MIT / Apache / BSD / ISC / Unlicense / MPL-2.0 file-level):**
faster-whisper, WhisperX, whisper.cpp, MoneyPrinterTurbo (patterns), video-use, openshorts, AI-Youtube-Shorts-Generator, CutScript, ai-video-captions, pycaps, KillerSubtitles, Beautiful-Captions, SubtitleEdit, libass, auto-editor (Unlicense), jumpcutter, silencecut-ffmpeg (BSD-2), auto-video-editor, rnnoise (BSD-3), OpenCut, OmniClip, OpenReel, Donkey (Apache), diffusionstudio/core + editor (MPL), mediabunny (MPL), hyperframes (Apache), ChatOctopus/timeline (MIT), OTIO (Apache), IndicXlit, indic_transliteration_py, IndicTrans2, Indic-Subtitler, moviepy.

**Reference only — DO NOT vendor code:**
AGPL: Audapolis, OpenChatCut, hotclip, pyvideotrans (GPL), subsai (GPL), VoiceStudio (GPL-3-adjacent/AGPL), Shotcut/OpenShot/Olive (GPL for the *apps* — but MLT XML/`melt` usage as an external process is fine).
Custom/unclear: **Remotion** (paid beyond 3 employees), openvideodev/react-video-editor (NOASSERTION), DeepFilterNet (NOASSERTION), buttercut/hyperedit (no license).

---

# RECOMMENDED BUILD PLAN

**Principle:** buy the commodity (ASR, media IO, UI chrome, subtitle rendering, NLE formats), build the differentiator (Indic romanization, retake removal, slang-robust word timing, preset pack).

| Stage | Reuse as-is | Steal algorithm/pattern | Write ourselves |
|---|---|---|---|
| **S1 Transcription** (word-level, Indic, code-switch) | **faster-whisper** (MIT) as the ASR worker; **WhisperX** (BSD-2) for word alignment + diarization; `whisper.cpp` if we later go on-device | MoneyPrinterTurbo's model-loading/caching + subtitle writer; CutScript's transcription service shape | Indic code-switching handling: language auto-detect per segment, slang/Colloquial correction pass (LLM or glossary), confidence scoring, per-language model routing |
| **S2 Romanization** (Thanglish/Hinglish/…) | **indic_transliteration_py** (MIT) for script normalization; **IndicXlit/Aksharantar** checkpoint (MIT) as a neural baseline; **IndicTrans2** (MIT) for native↔English translation | — | **Core IP:** the *stylistic* romanizer — natural Hinglish/Thanglish conventions (English word choice, hybrid spacing, code-mixed output), LLM-prompt layer + eval set per language, plus the "Text Magic" type-in/get-out editor UX |
| **S3 Silence removal** | ffmpeg `silencedetect` → invert → `select`/`aselect` (known-correct, no desync) | **auto-editor** (Unlicense) threshold/margin model; jumpcutter params; `silencecut-ffmpeg` (BSD-2) if TS backend | Word-gap-driven cutting on WhisperX timestamps (cut on transcript word gaps ≥ N ms, snap to word boundaries) so silence removal and text editing share one model |
| **S4 Retake removal** | — (nothing mature exists) | video-use's take-selection prompt + rules + EDL JSON; ScriptCutAI's script-alignment idea; ghost-editor assembly; MarioRodrigues10/shorts as a sanity check | **Differentiator #1:** sentence/phrase segmentation → cross-take similarity clustering (embedding) → best-take scoring (disfluency, duration vs script, ASR confidence, audio quality) → cut list merged with S3 |
| **S5 Animated captions** (12 viral styles) | **libass** (ISC) burn via ffmpeg `ass=` for the server path **or** HyperFrames (Apache-2.0) HTML→MP4; Mediabunny (MPL) for in-browser preview burn | **ai-video-captions** (MIT) preset set (Hormozi/MrBeast/Karaoke/…) + **pycaps** (MIT) CSS-template authoring model; Whisper→ASS `\kf` karaoke snippet; Beautiful-Captions line-break/auto-scale rules; MoneyPrinterTurbo's style config surface | Our 12-style preset pack (CSS/ASS), Indic font shaping (Devanagari/Tamil/Telugu conjuncts + Latin romanized text in one line), word-grouping tuned for romanized Indic |
| **S6 Audio cleanup** | ffmpeg `afftdn` + `loudnorm` baseline (free); **RNNoise** (BSD-3) for in-browser pass | DeepFilterNet quality bar (verify its "Other" license, or treat as a model to distill) | "Mic → studio sound" preset chain + loudness targets; per-language speech-band EQ tuning |
| **S7 NLE export** (Premiere/Resolve/Ae, 5 layers + SRT/VTT/TXT) | **ChatOctopus/timeline** (MIT) for FCPXML 1.8 + xmeml v5 + OTIO write; **OpenTimelineIO** (Apache-2.0) as internal interchange; ffmpeg for SRT/VTT/TXT | CLI-Anything's JSON→MLT XML skill if we add Kdenlive/Shotcut export | Our 5-layer timeline JSON schema (video, b-roll, music, voice, captions) and layer-splitting rules |
| **S8 Browser editor UI** | **@diffusionstudio/core** (MPL-2.0) as compositing/render engine; **Mediabunny** (MPL-2.0) for demux/mux/decode; **OmniClip** (MIT) timeline web-components and/or **OpenCut** (MIT) timeline UI patterns; ffmpeg.wasm (MIT) only for odd filters | OpenReel (MIT) agent-in-timeline UX; CutScript (MIT) transcript-editor component + `useVideoSync` hook + project schema; designcombo/react-video-editor layout (structure only) | The transcript↔timeline dual-pane UX itself, cloud/project persistence, Indian-language font rendering, and the export dialog |
| **Do NOT build on** | **Remotion** (license), **Etro** (GPL, legacy), **Audapolis/OpenChatCut/hotclip** (AGPL), Olive (dead), ShortGPT (stale), HF Spaces (toys), **mahseema/awesome-ai-tools** (SaaS directory, no code) | | |

**Build order (thin vertical slice first):**
1. **Slice 1 (proves the product):** upload → faster-whisper + WhisperX → word timestamps → silence removal on word gaps → SRT/VTT export. Reuses: faster-whisper, WhisperX, ffmpeg.
2. **Slice 2 (differentiator):** + romanization layer (IndicXlit baseline → LLM stylistic pass) → Thanglish captions in browser preview.
3. **Slice 3 (visual):** + 12 caption presets (steal from ai-video-captions/pycaps, render via libass or HTML/HyperFrames) → burned MP4.
4. **Slice 4 (moat):** + retake removal (write from scratch; seed logic from video-use/ScriptCutAI).
5. **Slice 5 (pro tier):** + audio cleanup chain + NLE export (ChatOctopus/timeline + OTIO) + full timeline UI on diffusionstudio/core.

**Bottom line:** ~60% of the pipeline is commodity code we can legally reuse under permissive/MPL licenses (ASR, alignment, media IO, caption engine, NLE formats, timeline UI). The two things nobody has open-sourced — **natural romanization of Indic speech into Hinglish-style Latin text** and **production-grade retake removal** — are exactly HyproAI's moat, and both must be written in-house.
