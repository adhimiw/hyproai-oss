# 07 — Audio Cleanup: Open-Source "Mic → Studio Sound" Stack

**Feature:** one-click AI noise reduction + loudness polish for Indian-language creators (no plugins, no settings).
**Target workload:** batch processing of a ~40-minute podcast on **CPU**, local/offline preferred, pip- or ffmpeg-only, **permissive license**, GPU optional, no paid APIs.

**Research method:** web search (GitHub, Hugging Face, arXiv/ISCA, blogs, Reddit), direct pulls of README/pyproject/LICENSE from raw GitHub + PyPI/HF JSON APIs, plus **local empirical tests** on this machine (ffmpeg n9.0.2, Python 3.14.8) — measured numbers are marked *(measured here)*.

---

## 0. Headline findings (read this first)

| # | Finding | Why it matters |
|---|---|---|
| 1 | **DeepFilterNet3** (Rikorose/DeepFilterNet, MIT/Apache-2.0, 5.0k★) is the best OSS "one-click" denoiser for our use: 48 kHz full-band, RTF **0.19** on a single-threaded laptop i5 *(published, Interspeech'23)* → a 40-min podcast ≈ **4–8 min on CPU**. | Core of the chain. |
| 2 | Its pip install **fails on Python ≥3.12** — `deepfilterlib` wheels only exist for **cp38–cp311** *(measured here: py3.14 → "metadata-generation-failed, needs rustup")*. | Pin the audio worker to a **3.10/3.11 venv**, or ship the **standalone Rust `deep-filter` binary** (zero Python deps) or the repo's **ONNX tarballs** + onnxruntime. |
| 3 | **ffmpeg `afftdn` is nearly free**: *(measured here)* 120 s of audio in **0.33 s → RTF ≈ 0.0027**; the full polish chain (HPF + compressor + EQ + loudnorm + limiter) = **1.94 s / 120 s → RTF ≈ 0.016**. | A complete non-neural fallback chain costs seconds for a 40-min episode. |
| 4 | **No OSS model does strong de-reverb cheaply.** DFN3 only does *mild* dereverb (its shipped config has `p_reverb = 0.1` augmentation; follow-up papers call dereverb a known limitation); real dereverb = diffusion/GPU (SGMSE+), non-commercial weights (NVIDIA NeMo) or research code. | Do **dereverb as best-effort inside DFN3** + optional heavy tier; don't promise "room removed". |
| 5 | **Adobe "Enhance Speech" is largely EQ + compression on top of a denoiser** (statement in Intel's Audacity-plugin discussion) — so the *ffmpeg polish + loudnorm* stage is what makes it sound "produced", not just the neural model. | Our chain = denoise → level → EQ, not denoise alone. |
| 6 | **DNSMOS (P.835) is the right auto-verifier**: no clean reference needed, directly predicts SIG/BAK/OVRL for "pick best of N variants". Two cheap routes: `torchmetrics` (Apache-2.0) or DeepFilterNet's built-in `dnsmos_local` (`pip install deepfilternet[dnsmos-local]`). | Enables a quality gate + automatic fallback. |
| 7 | Licenses to **avoid** for a commercial OSS product: **NVIDIA NeMo SE weights = CC-BY-NC-SA-4.0 (non-commercial)**, Intel Audacity plugins = GPL-3, SoX = GPL, `uformer` & `rnnoise-models` = **no license file**, `ffmpeg-normalize` = **no LICENSE file** (default all-rights-reserved), DNS-Challenge DNSMOS ONNX = **CC-BY-4.0 (attribution required)**. | See license table §6. |

---

## 1. Noise suppression

### 1.1 DeepFilterNet (DeepFilterNet1/2/3) — **primary candidate**

| field | value |
|---|---|
| name / repo | **DeepFilterNet** — https://github.com/Rikorose/DeepFilterNet |
| license | **dual MIT / Apache-2.0** (code *and* weights, "your choice") |
| stars / maintenance | **5.0k★**, 1,602 commits, 545 forks; active (0.5.7-pre on main); papers 2022–2023 (ICASSP, IWAENC, Interspeech) + 2025 extensions |
| what it does | Two-stage real-time full-band (48 kHz) speech enhancement: ERB-band mask + deep complex filtering → noise suppression **and mild dereverberation**; optional post-filter |
| install | `pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu` then `pip install deepfilternet` — **Python 3.8–3.11 only** *(verified constraint)*. No-Python alternative: prebuilt `deep-filter` binary from Releases, or `models/DeepFilterNet3_onnx.tar.gz` + `onnxruntime`, or `Intel/deepfilternet-openvino` (MIT IR files) |
| minimal CLI | `deepFilter noisy.wav` (writes `out/noisy.wav`; default model = **DeepFilterNet3**) · repo: `python DeepFilterNet/df/enhance.py -m DeepFilterNet3 --pf -o out/ noisy.wav` · flags: `--pf` (post-filter, harsher on very noisy parts), `--atten-lim 12` (cap attenuation), `-D` compensate latency |
| minimal python | `from df import enhance, init_df` → `model, df_state, _ = init_df(); out = enhance(model, df_state, noisy_48k_tensor)` |
| CPU speed | **RTF 0.19** single-thread i5-8250U *(DFN3, Interspeech'23 paper)*; DFN2 ≈ 0.42 on a Pi 4; independent stream benchmark **RTF 0.11**. i.e. 40-min episode ≈ **4–8 min CPU**, less if multi-threaded/chunked |
| I/O notes | Expects **48 kHz WAV**; `df/io.py` loads via `torchaudio` — pre-convert anything else with ffmpeg (`-ac 1 -ar 48000 -c:a pcm_s16le`) |
| dereverb | Config of shipped DFN3 (`models/DeepFilterNet3.zip → config.ini`, *fetched*) shows `[distortion] p_reverb = 0.1` → trained with reverb augmentation, **mild** dereverb only; 2025 follow-up paper explicitly frames dereverb as a limitation to extend |
| metrics built-in | Repo ships `df/sepm.py` (PESQ, STOI, composite, LLR, WSS) and `df/scripts/dnsmos.py` (**local DNSMOS**, downloads `sig.onnx`/`bak_ovr.onnx` from `microsoft/DNS-Challenge` at a pinned commit) |
| pros | Best OSS quality-per-CPU; permissive license incl. weights; ONNX/OpenVINO/Rust/Python/LADSPA deployment options; 48 kHz (keeps voice "air"); actively maintained |
| cons | Python wheels capped at 3.11 (must pin env or use binary/ONNX); dereverb weak; needs 48 kHz WAV input |

### 1.2 RNNoise (library) + ffmpeg `arnndn`

| field | value |
|---|---|
| repo | https://github.com/xiph/rnnoise (lib) · models: https://github.com/GregorR/rnnoise-models and https://github.com/richardpl/arnndn-models |
| license | **BSD-3-Clause** (+ CC0-1.0 for model per Fedora package metadata). ⚠️ The two community **model repos have no LICENSE file** ("license not specified" on GitHub) |
| stars / maintenance | 5.9k★ / 388★ / 54★; RNNoise mature/stable (classic, predates DFN), models last touched ~2018–2020 |
| what it does | 2018-era RNN noise suppressor at **16 kHz internal / 48 kHz I/O** via ffmpeg; cheap pitch-comb filtering |
| install | Ships inside ffmpeg as `arnndn`; model file must be downloaded separately (~300 KB `.rnnn`), **not bundled with ffmpeg or any distro package** |
| usage | `ffmpeg -i in.mp3 -af "aresample=48000,arnndn=m=/models/bd.rnnn:mix=0.85" out.mp3` (`bd` = voice w/ environment noise, `sh` = speech, `lq` = voice, `cb`/`mp` = general). Or link `librnnoise` directly / `./examples/rnnoise_demo` |
| CPU speed | Extremely light — realtime-capable on a single core of old hardware (no published RTF; far cheaper than DFN) |
| pros | Zero Python, in ffmpeg, tiny, battle-tested |
| cons | Quality clearly below DFN3 (artifacts, muffled highs, 16 kHz processing); **model-file licensing is murky** → risky to bundle; needs external `.rnnn` download in offline installs |

### 1.3 ffmpeg `afftdn` (classical, no model) — **fallback**

| field | value |
|---|---|
| repo | ffmpeg built-in filter (LGPL/GPL build) |
| license | Same as your ffmpeg build (LGPL 2.1+ configuration is fine) |
| what it does | FFT spectral-subtraction denoiser with tracking; no model file needed |
| install/usage | already in ffmpeg: `ffmpeg -i in.wav -af "afftdn=nr=15:nf=-45:rf=-40:tn=true" out.wav` — `nr` = noise reduction dB (default 12), `nf` noise floor, `rf` residual floor, `tn` track noise (auto-adapts to changing noise) |
| CPU speed | *(measured here)* 120 s → **0.33 s (RTF ≈ 0.0027)**; tuned params 0.365 s |
| pros | Trivially available, ~300× faster than realtime, no model, deterministic, no Python |
| cons | Noticeable musical-noise artifacts at `nr>18`, no neural quality, no dereverb |
| note | sibling filter `anlmdn` (non-local means) **segfaulted** in local ffmpeg 9.0.2 test → don't use untested |

### 1.4 noisereduce (Python, spectral gating)

| field | value |
|---|---|
| repo | https://github.com/timsainb/noisereduce · PyPI `noisereduce` 3.0.3 |
| license | **MIT** |
| stars | 1.9k★ (maintained, torch optional extra) |
| what it does | Stationary + non-stationary **spectral gating** noise gate (numpy/scipy; no model, no GPU) |
| usage | `import noisereduce as nr; clean = nr.reduce_noise(y=audio, sr=sr, stationary=True, y_noise=noise_clip_1s, prop_decrease=0.8)` |
| CPU speed | Fast (FFT-bound); no published RTF — expect ≪ RT for 48 kHz |
| pros | Zero model weights, dead simple, MIT, works on any Python |
| cons | Quality well below DFN3 (gating artifacts, musical noise); only *stationary* noise handled well; it's a gate, not a "studio" enhancer |
| verdict | Good **dependency-light fallback #2** (after afftdn), not a primary |

### 1.5 NVIDIA NeMo enhancement models

| field | value |
|---|---|
| repo | https://github.com/NVIDIA-NeMo → now https://github.com/NVIDIA-NeMo/Speech (code **Apache-2.0**, 19k★) |
| models | `nvidia/se_den_sb_16k_small` (denoise) and `nvidia/se_der_sb_16k_small` (dereverb) — Schrödinger-bridge generative U-Net, 25M params, 16 kHz |
| **license of the weights** | **CC-BY-NC-SA-4.0 — non-commercial, share-alike** ("This model is for research and development only") ⛔ |
| usage | `from nemo.collections.audio.models import AudioToAudioModel; model = AudioToAudioModel.from_pretrained('nvidia/se_der_sb_16k_small')` (needs NeMo 2.0, tested on V100) |
| verdict | ⛔ **Exclude** — code is Apache but **weights are non-commercial**, plus 16 kHz only, NeMo is a heavy dependency. Good paper reference (Schrödinger-bridge SE, Interspeech'24), not for shipping. |

### 1.6 Intel angle (DCCRN / OpenVINO)

- There is **no official Intel DCCRN repo**; DCCRN (Hu et al. 2020) is referenced only via third-party ports and appears as a *feature request* in Intel's Audacity plugin discussion (issue #382).
- **intel/openvino-plugins-ai-audacity** — https://github.com/intel/openvino-plugins-ai-audacity — **GPL-3.0**, 2.2k★ — ships denoise/speech-isolation models (incl. community DCCRN/Demucs ports) behind OpenVINO. Useful as *prior art* on how to bundle model packs; **GPL ⇒ don't link into our app**. Notable quote from its issue #126: *"Enhance speech of Adobe Podcast is just a EQ + compressor slapped onto your voice"* — validating our ffmpeg polish stage.
- ✅ The usable Intel asset: **`Intel/deepfilternet-openvino`** on HF — **MIT** OpenVINO IR of DeepFilterNet2/3 (CPU/NPU-friendly alternative to plain ONNX).

### 1.7 MP-SENet / CMGAN (DNS-challenge-style GAN models)

| | MP-SENet | CMGAN |
|---|---|---|
| repo | https://github.com/yxlu-0102/MP-SENet | https://github.com/ruizhecao96/CMGAN |
| license / stars | **MIT** / 510★ | **MIT** / 431★ |
| what it does | TF-domain parallel magnitude+phase denoising; **long version adds dereverb + bandwidth extension** (16 kHz) | Conformer metric-GAN denoiser + dereverb (16 kHz) |
| quality | PESQ **3.60** VB+DEMAND, 3.62 DNS-challenge set (SOTA-ish intrusive) | PESQ ~3.57 on VB+DEMAND |
| install/usage | clone + `pip install -r requirements.txt` + `python inference.py --checkpoint_file best_ckpt/g_best_vb --output_dir generated_files/MP-SENet` | research repo, manual checkpoints |
| HF weights | `JacobLinCool/MP-SENet-DNS` (**MIT**, 24k downloads) + pip pkg `mpsenet` (JacobLinCool/MPSENet) | none official |
| CPU speed | Not published; a full conformer+HiFiGAN generator → expect **≥1× RT on CPU** (much slower than DFN3) | similar |
| pros | Highest intrusive scores; dereverb + BWE in one model; MIT; HF-hosted weights | MIT, good dereverb |
| cons | 16 kHz only (losses "air"), heavier, research-grade inference code (manual checkpoints), lower community adoption than DFN | research code, 16 kHz, no packaged CLI |

### 1.8 SGMSE / SGMSE+ (diffusion) & "SGMBSE"

- **repo:** https://github.com/sp-uhh/sgmse (MIT, 776★, Univ. Hamburg) — "Speech Enhancement with Score-Based Generative Models"; SGMSE+ = 48 kHz extension; does **denoise + dereverb**; weights also at `speechbrain/sgmse-voicebank` (Apache-2.0).
- **"SGMBSE"** as a term does not resolve to a separate well-known repo — it maps to this score-based/generative SE family (papers 2022–2025, e.g. `sp-uhh/gen-se`, `sp-uhh/sgmse_crp` single/few-step variants).
- **CPU:** diffusion sampling (often 100–1000 NFE) ⇒ **many × real-time on CPU**; GPU-tier only. Research-grade CLI (needs a base dir + config).
- **verdict:** quality is excellent for heavy noise/reverb, but ❌ for a CPU batch pipeline; keep as optional GPU tier.

### 1.9 Hugging Face inference routes (no generic `pipeline("enhancement")` exists)

`transformers` has **no** built-in enhancement task; the HF patterns actually used are:

| model | license | notes |
|---|---|---|
| `mispeech/dasheng-denoiser` (xiaomi-research/dasheng-denoiser, 82★) | **Apache-2.0** | `AutoModel.from_pretrained(..., trust_remote_code=True)`; Interspeech'25; **16 kHz only**; `pipeline_tag: audio-to-audio` |
| `JacobLinCool/MP-SENet-DNS` | MIT | PytorchModelHubMixin; 16 kHz denoise+dereverb |
| `ResembleAI/resemble-enhance` | **MIT** | weights for the `resemble-enhance` CLI (44.1 kHz) |
| `Intel/deepfilternet-openvino` | MIT | ONNX/OpenVINO IRs of DFN2/3 |
| `speechbrain/metricgan-plus-voicebank` | Apache-2.0 | 11.7k downloads; SpeechBrain `Enhancement.from_hparams` |
| `iky1e/DeepFilterNet3-MLX`, `mlx-community/DeepFilterNet-mlx` | MIT | Apple-silicon ports (not our target) |
| demos | — | HF Spaces `hshr/DeepFilterNet2`, `ResembleAI/resemble-enhance`, `alibabasglab/ClearVoice`, `alibabasglab/SpeechScore` |

**Misc queries from the brief:**
- **"Moss (Meta)"** → there is **no Meta "Moss" audio-enhancement model**. `MossFormer/MossFormer2` (incl. `MossFormer2_SE_48K`) is **Alibaba DAMO**, shipped via ClearVoice. OpenMOSS (`MOSS-TTSD`, `MOSS-TTS`, Apache-2.0) is text-to-speech/dialogue — unrelated.
- **"denoise-override"** → Stable-Diffusion-UI parameter name; **nothing to do with audio**.

---

## 2. De-reverb

| candidate | repo | license / stars | what it does | CPU-friendly? | verdict |
|---|---|---|---|---|---|
| **DeepFilterNet3** | Rikorose/DeepFilterNet | MIT/Apache · 5.0k★ | mild dereverb learned via `p_reverb=0.1` RIR augmentation (config verified) | ✅ RTF 0.19 | **Our default (best-effort)** |
| **Demucs (htdemucs / htdemucs_6s)** | https://github.com/facebookresearch/demucs | **MIT** · **10k★** | 4/6-stem separation; `--two-stems=vocals` removes music/BGM/beds so only voice remains | ⚠️ ~1.5–4× RT on CPU (community: 8–15 min per 4-min track) | ✅ for **"strip background music/noise beds"**, ❌ reverb stays in the vocal stem |
| **nara_wpe (WPE)** | https://github.com/fgnt/nara_wpe | MIT · 577★ | Weighted Prediction Error blind dereverberation (numpy/tf), offline + online | ✅ but strongest on **multi-channel**; mono WPE = limited | ⚠️ only if we ever get mic-array/stereo material |
| **Uformer** | https://github.com/felixfuyihui/uformer | ⚠️ **license not specified** · 117★ | U-Net dual-path conformer for joint SE+dereverb | research code | ❌ license ambiguity + low adoption |
| **SGMSE(+)** | sp-uhh/sgmse | MIT · 776★ | diffusion dereverb+denoise | ❌ GPU | optional GPU tier |
| **NeMo `se_der_sb_16k_small`** | NVIDIA-NeMo/Speech | ⛔ **CC-BY-NC-SA** | Schrödinger-bridge dereverb | GPU-oriented | ❌ license |
| **VoiceFixer** | https://github.com/haoheliu/voicefixer | MIT · 1.4k★ | general restoration: noise + reverb + 2 kHz–44.1 kHz bandwidth + clipping (44.1 kHz neural vocoder) | heavy, older (2021) | ⚠️ niche: rescuing *very* degraded/old audio |
| **ClearVoice MossFormer2_SE_48K** | modelscope/ClearerVoice-Studio | Apache-2.0 · 4.6k★ | denoise (+ optional SE-48K clarity) | transformer @48 kHz → slow on CPU | alternative enhancer, not a dereverb specialist |

**Practical conclusion:** for a CPU one-click flow, *do not* build a dedicated de-reverb stage. Rely on DFN3's partial dereverb, detect severe reverb with a non-intrusive metric (**SRMR**, in ClearerVoice `SpeechScore`) and, if severe, either (a) warn "room echo remains", or (b) route to an optional GPU tier (resemble-enhance / SGMSE+).

---

## 3. Loudness normalization, dynamics & EQ polish (ffmpeg-only, cheap)

All measured present in local ffmpeg 9.0.2: `highpass, lowpass, equalizer, firequalizer, treble, bass, acompressor, deesser, alimiter, asoftclip, dynaudnorm, speechnorm, loudnorm, afftdn, arnndn, anlmdn`.

| tool | license | role | notes |
|---|---|---|---|
| **`loudnorm` (EBU R128)** | ffmpeg | integrated loudness target | **two-pass for podcasts**: pass 1 `print_format=json` → pass 2 with `measured_*` + `linear=true` for glitch-free gain. Targets: **I=-16, TP=-1.5, LRA=11** (podcast standard; use `I=-14` for YouTube) |
| **`acompressor`** | ffmpeg | "voice leveling" (tames loud/quiet swings) | `threshold=-20dB:ratio=2.5-3:attack=15-20:release=250:makeup=4-6` — this is the "Auphonic-style" leveler in one line |
| **`speechnorm` / `dynaudnorm`** | ffmpeg | fast speech leveling / dynamic normalizer | lighter-weight alternatives to `acompressor` when you want aggressive leveling of very uneven takes |
| **`firequalizer` / `equalizer` / `treble`+`bass`** | ffmpeg | EQ polish | gentle voice curve: HPF 75–80 Hz, −3 dB rumble <100 Hz, +2–3 dB presence 3–6 kHz, shelf −1..−2 dB >10 kHz if sibilant; `firequalizer=gain='if(lt(f,100),-4,if(lt(f,3000),0,if(lt(f,6000),+2.5,-1)))'` |
| **`deesser` + `alimiter`** | ffmpeg | sibilance + true-peak safety | `alimiter=limit=0.97` after loudnorm |
| **ffmpeg-normalize** | https://github.com/slhck/ffmpeg-normalize · 1.5k★ | batch 2-pass loudness CLI | ⚠️ **no LICENSE file in repo / empty PyPI license field** → treat as all-rights-reserved; **implement the 2-pass loudnorm ourselves** (5 lines) instead of depending on it |
| **SoX `compand`/`noisered`** | GPL-2.0-or-later | classic leveling | ❌ GPL — reference only, don't link |
| **Speex / libspeexdsp** | BSD-3 | cheap DSP noise suppression + AGC | legacy quality (worse than RNNoise); only relevant if we ever need a C-only, model-free path |
| **speex `speex_preprocess`** | BSD-3 | VAD-based gating/AGC | fallback-only |

*(measured here)* **Cost of the whole polish stage on CPU** (HPF + compressor + 2 peaking EQ + loudnorm pass-2 + limiter, 120 s @48 kHz): **1.94 s total (RTF 0.016)**; loudnorm measurement pass: 1.47 s. For a 40-min episode: **~45 s**. Negligible vs. the neural stage.

---

## 4. One-click "studio sound"-style OSS (the closest things to Adobe Enhance / Descript Studio Sound)

| project | repo / weights | license · stars | what it gives you | CPU batch viability |
|---|---|---|---|---|
| **DeepFilterNet3 + ffmpeg polish** (ours) | Rikorose/DeepFilterNet | MIT/Apache · 5.0k★ | denoise + mild dereverb + level + EQ + loudness | ✅ **RTF 0.19** → 40-min ≈ 4–8 min |
| **Resemble Enhance** | https://github.com/resemble-ai/resemble-enhance · HF `ResembleAI/resemble-enhance` | **MIT** · 2.5k★ | two modules: **denoiser** + **enhancer** (44.1 kHz latent **flow-matching** restoration/bandwidth extension) — the closest OSS "make it sound produced" | ⚠️ GPU tier: `nfe=64` sampling; MLX note says PyTorch **CPU reference is ~7.5× slower** than M4 Max GPU. Great *optional* high tier |
| **ClearVoice / ClearerVoice-Studio** | https://github.com/modelscope/ClearerVoice-Studio · HF `alibabasglab/*` | **Apache-2.0** · 4.6k★ | unified `pip install clearvoice`: `MossFormer2_SE_48K`, `FRCRN_SE_16K`, `MossFormerGAN_SE_16K`, plus **speech super-resolution (48 kHz)** and **SpeechScore metrics** | ⚠️ 48 kHz transformer is CPU-heavy; FRCRN 16k lighter. Manual download fallback via ModelScope if HF blocked (good for India/offline) |
| **Intel OpenVINO Audacity plugins** | intel/openvino-plugins-ai-audacity | ⚠️ **GPL-3** · 2.2k★ | ready-made denoise + "speech isolation" model packs, CPU-optimized | ✅ but GPL → reference/prior art only |
| **EasyEffects / NoiseTorch / PulseEffects** | GPL-3 | realtime PipeWire/LADSPA hosts with RNNoise/DFN | ✅ as apps, ❌ as libraries (GPL, Linux-only) — proves the UX pattern: one toggle, no settings |
| **dasheng-denoiser** | xiaomi-research/dasheng-denoiser · HF | Apache-2.0 · 82★ | minimal `AutoModel` denoise, 16 kHz | ✅ but 16 kHz, young project |
| **MP-SENet / CMGAN / SGMSE** | see §1.7–1.8 | MIT | best intrusive scores | ⚠️ research CLIs |
| paid/proprietary (context only) | Adobe Enhance Speech, Descript Studio Sound, Cleanvoice, Auphonic, Krisp | — | benchmark for what "studio sound" means | — |

---

## 5. Quality metrics for auto-verification (picking best-of-N variants)

| metric | package | license | needs reference? | use here |
|---|---|---|---|---|
| **DNSMOS P.835** (p808, **SIG, BAK, OVRL**) | **`torchmetrics`** → `torchmetrics.audio.dnsmos.DeepNoiseSuppressionMeanOpinionScore(fs=16000, personalized=False)`; requires `pip install torchmetrics[audio]` (librosa+onnxruntime) | code **Apache-2.0**; **ONNX weights from `microsoft/DNS-Challenge` = CC-BY-4.0 → attribute** | **No** | ✅ **primary gate**: score original vs each candidate, pick max OVRL with SIG guard |
| DNSMOS (built-in alt) | `pip install deepfilternet[dnsmos-local]` → `df/scripts/dnsmos.py::dnsmos_local(audio, sig.onnx, bak_ovr.onnx)` (auto-downloads ONNX from a pinned DNS-Challenge commit) | MIT code / CC-BY-4.0 weights | No | ✅ zero-extra-dep if DFN is already installed |
| DNSMOS (tiny alt) | `pip install speechmos` (Microsoft, DNS/AEC/PLC MOS) · HF mirror `TigreGotico/dnsmos-onnx` | weights CC-BY-4.0 | No | ✅ lightest option (no torch) |
| **PESQ** | `pip install pesq` (ludlows/python-pesq, 634★) → `pesq(16000, ref, deg, 'wb')` (alt: `pypesq`) | **MIT** | Yes | regression tests / offline A-B with a clean fixture set |
| **STOI** | `pip install pystoi` → `stoi(ref, deg, sr, extended=False)` (363★) | **MIT** | Yes | intelligibility regression (Indian-accent/phoneme-safe check) |
| **SI-SDR** | `torchmetrics.ScaleInvariantSignalToDistortionRatio` or `torchaudio.functional.si_sdr` | Apache-2.0 / BSD | Yes | distortion guard (detect over-processing) |
| **COMPOSITE (CSIG/CBAK/COVL), LLR, WSS, fwSNRseg** | `df/sepm.py` (in DeepFilterNet) or ClearerVoice **SpeechScore** | MIT / Apache-2.0 | Yes | richer offline analysis |
| **SRMR** (reverb, non-intrusive) | ClearerVoice SpeechScore (`SRMR`) / `SRMRpy` | Apache-2.0 | **No** | ✅ **reverb detector** → decides whether to route to the "heavy dereverb" tier |
| **NISQA / DISTILL-MOS** | ClearerVoice SpeechScore (added 2025.4–5) | Apache-2.0 | No | secondary no-reference sanity check |
| SQUIM (torchaudio) | `torchaudio.pipelines.SQUIM_OBJECTIVE` | BSD-2 | predicts metrics from degraded input | optional, torch-only |

**How to use for best-of-N (cheap):** run N=3–4 candidate chains on **3 × 30-s excerpts** (speech-dense, one clean-ish, one noisy, one reverberant) → score each with DNSMOS (SIG/BAK/OVRL) + SRMR → pick the winner **with guards**: `OVRL(win) ≥ OVRL(input) + 0.10` **and** `SIG(win) ≥ SIG(input) − 0.15` (avoid over-suppression artifacts) → apply the winner once to the full file → final DNSMOS pass on 3 windows of the output (assert improvement persisted). ~15 s of audio per variant keeps metric time negligible.

---

## 6. License / compliance table

| component | license | ship? |
|---|---|---|
| DeepFilterNet (code + weights) | MIT / Apache-2.0 dual | ✅ |
| RNNoise library | BSD-3-Clause | ✅ |
| `.rnnn` models (GregorR/rnnoise-models, richardpl/arnndn-models) | **no LICENSE file** | ⚠️ avoid bundling; if used, verify with counsel |
| ffmpeg (`afftdn`, `arnndn`, `loudnorm`, EQ/comp) | LGPL/GPL (build-dependent) | ✅ (system ffmpeg / LGPL build) |
| noisereduce | MIT | ✅ |
| Demucs | MIT | ✅ |
| ClearerVoice-Studio / ClearVoice / MossFormer2_SE weights | Apache-2.0 | ✅ |
| Resemble Enhance (code + weights) | MIT | ✅ |
| MP-SENet, CMGAN, SGMSE | MIT | ✅ |
| SpeechBrain (+ metricgan-plus, sgmse-voicebank weights) | Apache-2.0 | ✅ |
| dasheng-denoiser (Xiaomi) | Apache-2.0 | ✅ |
| VoiceFixer | MIT | ✅ |
| torchmetrics, onnxruntime, torchaudio, pesq, pystoi | Apache-2.0 / MIT / BSD | ✅ |
| **NVIDIA NeMo SE/DER weights** | **CC-BY-NC-SA-4.0** | ⛔ non-commercial |
| DNSMOS ONNX (microsoft/DNS-Challenge) | CC-BY-4.0 | ✅ with attribution line in UI/About |
| Intel OpenVINO Audacity plugins | GPL-3.0 | ⛔ (linking) |
| SoX, EasyEffects/NoiseTorch | GPL | ⛔ as library |
| uformer, MIMO-speech-enhancement | not specified | ⚠️ avoid |
| ffmpeg-normalize | **no license file** | ⚠️ reimplement 2-pass loudnorm |
| MossFormer = Alibaba, MOSS = OpenMOSS TTS | — | (clarifications, see §1.9) |

---

## 7. RECOMMENDED PICK

### 7.1 One-click chain (exact order & commands)

```
Stage 0  normalize input  →  mono 48 kHz WAV            (ffmpeg)
Stage 1  neural denoise + mild dereverb                 DeepFilterNet3  (MIT)
Stage 2  voice leveling: HPF → compressor → de-esser    (ffmpeg acompressor)
Stage 3  EQ polish (warmth + presence)                  (ffmpeg firequalizer/equalizer)
Stage 4  loudness: two-pass EBU R128 → -16 LUFS         (ffmpeg loudnorm) + limiter
Stage 5  verify with DNSMOS, auto-fallback if worse     (torchmetrics or df dnsmos_local)
```

**Stage 0 — input prep**
```bash
ffmpeg -y -i "$IN" -vn -ac 1 -ar 48000 -c:a pcm_s16le work.wav
```

**Stage 1 — DeepFilterNet3 (preferred)**
```bash
# Python env (MUST be 3.10/3.11 — deepfilterlib wheels stop at cp311):
python3.11 -m venv venv && venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
venv/bin/pip install "deepfilternet[dnsmos-local]"
# One click:
venv/bin/deepFilter --pf -o enhanced/ work.wav        # writes enhanced/work.wav
```
Alternatives when the pip path is unavailable (offline/image-constrained builds), in priority order:
1. **Rust binary**: download `deep-filter` from the GitHub Releases page → `deep-filter --pf -o enhanced/ work.wav` (no Python at all);
2. **ONNX**: `models/DeepFilterNet3_onnx.tar.gz` (also `_ll_` low-latency variant; DFN2 `*_onnx.tar.gz`) with `onnxruntime` (MIT) — 2 files + ~40 lines of glue, no torch;
3. **OpenVINO**: `Intel/deepfilternet-openvino` (MIT) for Intel CPU/NPU.

**Stage 2–4 — polish + loudness (single ffmpeg pass after measuring)**
```bash
# Pass 1: measure (run on the ENHANCED file)
ffmpeg -i enhanced/work.wav -af loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json -f null - 2> stats.json

# Pass 2: apply everything (paste measured_* from stats.json)
ffmpeg -y -i enhanced/work.wav -af "\
highpass=f=78, \
acompressor=threshold=-20dB:ratio=2.8:attack=20:release=250:makeup=4, \
deesser, \
firequalizer=gain='if(lt(f,110),-4, if(lt(f,3000),0, if(lt(f,6500),+2.5, -1)))', \
loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=..:measured_TP=..:measured_LRA=..:measured_thresh=..:offset=..:linear=true, \
alimiter=limit=0.97:level=false" \
-ar 48000 -ac 1 -c:a pcm_s16le final.wav
```
(Order matters: **denoise → level → EQ → loudness → limiter**; measuring loudness *after* dynamics keeps the LUFS number truthful. Swap `I=-14` for YouTube-style targets. Cost ≈ **45 s per 40-min episode** *(measured RTF 0.016)*.)

**Stage 5 — auto-verification & fallback ladder**
```python
# pip install torchmetrics[audio]   (Apache-2.0 code; DNSMOS ONNX = CC-BY-4.0 → add attribution)
from torchmetrics.audio.dnsmos import DeepNoiseSuppressionMeanOpinionScore
m = DeepNoiseSuppressionMeanOpinionScore(fs=16000, personalized=False)   # -> [p808, SIG, BAK, OVRL]
def score(wav16k): return m(wav16k).tolist()
```
Candidates scored on 3 × 30-s excerpts: **v0** original · **v1** ffmpeg-only (§7.2) · **v2** DFN3-only · **v3** DFN3 + polish chain.
Winner = argmax **OVRL** subject to `SIG_win ≥ SIG_input − 0.15` and `OVRL_win ≥ OVRL_input + 0.10`; also require **SRMR not worse** (reverb guard). If no candidate passes, ship **v1** (ffmpeg-only) rather than the original only when `BAK` improved; otherwise ship the original unchanged (never make it worse).

### 7.2 Fallback if models are unavailable (ffmpeg-only, zero Python, offline-proof)

```bash
ffmpeg -y -i "$IN" -vn -ac 1 -ar 48000 -c:a pcm_s16le -af "\
highpass=f=78, \
afftdn=nr=15:nf=-45:rf=-40:tn=true, \
acompressor=threshold=-20dB:ratio=2.8:attack=20:release=250:makeup=4, \
firequalizer=gain='if(lt(f,110),-4, if(lt(f,3000),0, if(lt(f,6500),+2.5, -1)))', \
loudnorm=I=-16:TP=-1.5:LRA=11, \
alimiter=limit=0.97" \
-af 2>/dev/null || true
# (two-pass loudnorm recommended in practice; single-pass costs quality but never fails)
```
- Cheaper denoise if `afftdn` artifacts are objectionable: `arnndn=m=/models/bd.rnnn:mix=0.85` (bundle the `.rnnn` only after license clearance — see §6).
- Leveling-only micro-fallback (very old CPU): drop `afftdn`, keep `dynaudnorm=f=200:g=7:p=0.54` or `speechnorm` before `loudnorm`.
- *(measured here)* this whole fallback = **≈2 s per 120 s of audio (RTF 0.016)** → ~45 s for a 40-min podcast.

### 7.3 Optional GPU tier ("wow" mode, still offline)

- **Resemble Enhance** (MIT, 44.1 kHz): `pip install resemble-enhance && resemble-enhance in_dir out_dir` — denoise + flow-matching enhancement for the strongest "studio" impression when a GPU is present (CPU is ~7× slower than GPU, so gate it behind a "high quality (GPU)" toggle).
- **Demucs `--two-stems=vocals`** (MIT) when the episode has music beds/jingles the user wants removed: `demucs --two-stems=vocals -n htdemucs -o out input.mp3`.
- Strong reverb + GPU: **SGMSE+** (MIT) or (if acceptable) MP-SENet long-version dereverb checkpoint.

### 7.4 Sizing summary (40-min podcast, CPU only)

| stage | expected wall time |
|---|---|
| prep (ffmpeg) | ~5 s |
| DeepFilterNet3 (RTF 0.19, single thread) | **4–8 min** |
| polish + 2-pass loudnorm (RTF 0.016) | ~45 s |
| DNSMOS verification (4 variants × 90 s audio) | ~10–20 s |
| **total (model path)** | **≈ 5–10 min** |
| **total (ffmpeg-only fallback)** | **≈ 1 min** |

### 7.5 Implementation notes / risks

1. **Pin Python 3.10–3.11** for the audio worker (or use the `deep-filter` binary/ONNX) — verified blocker on 3.14.
2. Pre-convert to **mono 48 kHz PCM** (DFN expects 48 kHz; DNSMOS wants 16 kHz → resample only for scoring).
3. Chunk long files if RAM is tight (DFN handles a full file, but 40 min @48 kHz float ≈ 460 MB/arrays) — 60-s windows with 1-s overlap, crossfade at seams.
4. Ship a **DNSMOS attribution** line (CC-BY-4.0) if using the Microsoft ONNX models; everything else in the core chain is MIT/Apache/BSD.
5. Never let the pipeline output something *worse* than input: DNSMOS guard in Stage 5 is mandatory, not optional.
6. UI: expose **exactly one button** ("Enhance") + an advanced "strength" only if needed (weak: `afftdn` nr=10 + light EQ; standard: DFN3 + chain; strong: + Resemble Enhance on GPU).
