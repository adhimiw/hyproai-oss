# HyproAI — Product Spec & Competitive Research

**Date:** 2026-11-04
**Sources:** hyproai.com landing (full HTML + JSON-LD), sitemap.xml, legal pages, blog (via search; blog origin down), production JS bundles from `editor.hyproai.com`, BuiltWith, crt.sh, search results (Peerlist, Supercaptions, LinkedIn).
**Scope note:** HyproAI has no features/pricing subpages — everything is on one landing page. The authoritative pricing/feature data was extracted from the app's production bundle (`index-*.js`), which is more precise than the marketing copy.

---

## (a) Feature Inventory

### Core AI editing pipeline
| Feature | Detail |
|---|---|
| **Auto-captions / subtitles** | Word-level timestamps; 33 languages + Auto-Detect; 3 output modes: Native Script, Romanized, English Translation |
| **Transcription provider** | Google Cloud Speech-to-Text **Chirp** (`transcriptionMethod` / `transcriptionProvider: "chirp"`) |
| **Text Magic (romanization)** | Tamil→Thanglish, Hindi→Hinglish, Telugu→Telugish, Kannada→Kanglish, Malayalam→Malayalish, Bengali→Benglish; auto-selected on native-script captions |
| **Silence Remover** | Smart pass (auto) or Custom pass (min-pause seconds); silence ranges removed or replaced with a chosen background |
| **Retake Remover** | Two settings, keeps best take, removes flubbed lines |
| **Audio Enhance** | Rebalance dialogue against music (ducking), dynamic processing, RMS loudness analysis (UI in `editor.js`, gated feature) |
| **Video Enhance** | Auto Zoom + Filters (gated feature) |
| **Auto Zoom & Filters** | Caption-driven zoom punches, filters |
| **Auto SFX** | Sound effects auto-added on captions (`captionAutoSfx`) |
| **Stock library** | Pexels videos (auto-detected API key, `media.hyproai.com` proxy) + Pexels photos |
| **Text & stickers** | Full editing timeline with split/trim/snap, keyboard shortcuts |
| **B-roll** | Stock b-roll layer in timeline |
| **Custom fonts** | Upload TTF/OTF/WOFF/WOFF2 (gated: `custom-fonts`) |
| **Caption styles** | ~12 preset styles: Hypro Special, Gradient Base, Sapphire Script, Neon Emerald, Sunset Script, Classic Gold, Delhi, Quick Brown, etc. + custom presets (gated) |
| **Caption animations** | Slide-up, glow, word-by-word |
| **Caption rules** | Max length default 35 chars, min duration 0.5s, gap 0, Single Word / Double Line modes, safe-zone |
| **Podcast editor** | Multi-track podcast layout (gated: `podcast`) |
| **Multicam editor** | Multicam sync view (gated: `multicam`) |
| **NLE export** | Premiere XML / Resolve XML / FCPXML / AE EDL; 5 layers: video, b-roll, music, voice, captions; single-ZIP or two-file modes (gated: `nle-export`) |
| **Subtitle export** | SRT / VTT / TXT download (gated: `subtitle-export`) |
| **Cloud editing** | Browser-based; timeline autosaves; media uploaded to R2 (`media.hyproai.com`) |
| **No watermark** | Explicit "No watermark" |
| **Export** | MP4 (resolution capped by plan), up to 4K; client-side render via Remotion + WebCodecs (`avc1.4d001f`, `client-encode-worker`, software fallback); AWS Lambda server renderer also present |
| **Desktop apps** | macOS 13+ arm64 + x64 (DMG), Windows x64 (EXE), via `/api/desktop/releases/latest/manifest.json` |
| **PWA / mobile** | Installable PWA; mobile editor in `beta.hyproai.com` (Android/iOS download buttons in bundle) |
| **In-app tutorials** | 9 tutorials (workflow section below) |

### Gating (plan → feature) map from `featureGate`
| Gate key | Feature | Plan tiers that unlock (from pricing) |
|---|---|---|
| `silence-remover` | Silence Remover | Daily Pass + |
| `retake-remover` | Retake Remover | Daily Pass + |
| `audio-enhance` | Audio Enhance | Daily Pass + |
| `video-enhance` | Video Enhance (Auto Zoom + Filters) | Creator + |
| `nle-export` | NLE project export | Daily Pass + |
| `subtitle-export` | SRT/VTT/TXT export | Daily Pass + |
| `podcast` | Podcast editor | Creator + |
| `multicam` | Multicam editor | Creator + |
| `custom-fonts` | Custom font upload | Creator + |
| `custom-presets` | Custom caption presets | Creator + |
| `teamPerms` | Team permissions per teammate | Agency (5 seats) |

Plan labels include `Free`, `Daily Pass`, `Influencer Pass`, `Starter`, `Creator`, `Agency`.

### Dashboard / project management
- Create project → name + upload (MP4, MOV, WebM, MP3, WAV; max **10 min** total, plan-sized storage budget)
- Dashboard shows per-project: thumbnail, title, duration, status, `createdAt`, storage usage bar
- Announcements panel (`/api/announcements/current`)
- Profile with subscription; usage reconcile (`/api/profile/reconcile`, `/api/usage/consume`)
- Client error reporting (`/api/client-error`)

---

## (b) Exact Pricing Matrix (final prices, INR/month unless noted)

### Plans
| Plan (code) | Price | Included minutes | Storage | Resolution | Exports | Seats | Notes |
|---|---|---|---|---|---|---|---|
| **Free** (`free`) | ₹0 | **5 min/month** | 5 GB (auto-delete after 7 days) | max **720p** | **1 export/month**, max 1 min | 1 | Transcribe + Romanize + Translate only; **no subtitle export**; no watermark |
| **Daily Pass** (`daily-pass`) | **₹89/day** | **10 min/day** | 5 GB, 7-day expiry | up to 1080p | up to 10/month | 1 | Captions, silence remover, audio enhance, Premiere/Resolve captions + jump cuts, SRT/VTT/TXT, up to 10 exports |
| **Influencer Pass** (`influencer`) — legacy | redeem code | 8 min | 5 GB, **30-day** storage | 1080p | 2 exports of 1 min | 1 | Redeemed via `/api/influencer/redeem`; 30-day access; no add-ons |
| **Starter** (`starter`) | **₹399/mo** | **25 min** (~₹15.96/min) | 10 GB permanent | up to 1080p | unlimited | 1 | + subtitles export, silence remover, audio enhance |
| **Creator** (`creator`) | **₹799/mo** | **80 min** (~₹9.99/min) | 30 GB permanent | up to **4K** | unlimited | 1 | + Retake Remover, Video Enhance, custom fonts & presets, Podcast/Multicam editors, NLE export, priority AI |
| **Agency / Pro** (`pro`) | **₹2,499/mo** | **250 min** (~₹10/min) | 100 GB permanent | up to **4K** | unlimited | **5 team seats** | Everything in Creator + team seats; (internal code name `pro`, public label `Agency`) |

- All plans: "No watermark" on every export.
- Storage: Free/Daily Pass = auto-deleted after 7 days (Influencer: 30 days). Paid Starter+ = permanent until plan lapses; lapse → delete/pause prompt.
- Minutes are "content minutes used for AI processing (transcription, silence removal, retake detection, export)".

### Add-ons (per month unless noted)
| Add-on | Sizes | Prices | Unit price |
|---|---|---|---|
| **Minutes** | 5 / 10 / 25 / 50 / 100 | ₹50 / ₹100 / ₹250 / ₹500 / ₹1,000 | **₹10/min** (never expires) |
| **Team seats** | 1 / 3 / 5 / 10 | ₹499 / ₹1,299 / ₹1,999 / ₹3,499 | per seat/month |
| **Storage** | 10 / 25 / 50 / 100 / 200 GB | ₹30 / ₹75 / ₹150 / ₹300 / ₹600 | **₹3/GB**, valid 30 days, no auto-renew |

- Add-ons unavailable on Free/Daily Pass/Influencer (except storage is offered in some promo path).
- No annual/discounted multi-month tier in code — only monthly + daily.

### Payment / billing mechanics
- **Cashfree Payments** v3 (`https://api.cashfree.com/pg`, `pg.js`) + **RBI e-mandate AutoPay** for subscriptions (cashfree SDK v0.3.25)
- **Phone-OTP login gate** (phone + OTP), Firebase Auth, Cloud Firestore, Cloud Storage
- Refund policy: 3-day no-questions window; liability capped at fees paid in last 3 months; governed by Chennai courts (TN, India)

### Marketing claims (landing)
- ₹0 free plan; "No watermark"; "Up to 4K" (Creator+)
- 4.9 rating / 5.0, "10,000+ creators", "15,000+ happy customers", "3x faster, 2x cheaper than Descript" (descriptive marketing comparison)

---

## (c) Target User & Positioning

**Primary persona: Indian regional-language content creators / YouTubers / Instagram reels creators / podcasters / short-form editors** who shoot in Hindi, Tamil, Telugu, Kannada, Malayalam, Bengali, etc., and want:
1. Captions in **English letters** (romanization: Hinglish, Thanglish…) rather than native script
2. One-click silence + retake cleanup (the "jump cut" workflow without manual editing)
3. Export into a real NLE (Premiere/Resolve/Final Cut) when they need professional finishing
4. Cloud, no-install, browser-only editing (vs. Descript's desktop app / CapCut's install)
5. Cheap per-minute economics for high-volume output

**Positioning statements (verbatim from landing):**
- "AI edits for everyone"
- "The fastest way to caption & edit your videos"
- "World's first silence remover, retake remover & regional subtitles in English letters"
- "Built for Indian creators, loved by 10,000+ creators" / "The Indian alternative to Descript"
- "Everything you need, nothing you don't"
- "AI edits your video. You just review." / "Skip the AI slop. It's your video, not a template."

**Differentiators vs. Descript/CapCut (per landing):**
- Browser-only (no install, works on any device)
- Indian regional language romanization as a first-class feature
- Pay ₹89/day for a single project instead of ₹2,400+/mo (Descript positioning claim)
- Auto Zoom & Filters / Auto SFX / caption-driven editing as "magic"

**Social proof / third-party:**
- Testimonial claim: *"I ran this against Descript and CapCut for 2 weeks. HyproAI is ahead by a mile"*
- Peerlist project by `bloggeronblue`: "HyproAI - Edit in your language" — web app for creators/podcasters/short-video editors with **10x faster workflow**, built with React + Tailwind
- Registry: HYPROAI PRIVATE LIMITED, incorporated 19 Dec 2025, CIN U58200TN2025PTC187492, Ambattur/Chennai TN, GST 33AAICH3698E1ZG
- Founders: Tharun Kumar (founder, ex-Kanan.co, Virtuatainment), Nizam Uddin (700K IG followers, The Crown Media, Pro Skill Academy)
- Company: made in Tamil Nadu, India; `info@hyproai.com`
- No Product Hunt launch found; blog is a WordPress instance (blog.hyproai.com, currently origin-down/522) with SEO posts (Malayalam subtitle generator, trending Instagram hashtags) + spam content

---

## (d) Tech Stack Clues

**Frontend (editor app, `editor.hyproai.com`)**
- React 19 + TypeScript + Vite (SPA, manifest.json, PWA), Tailwind CSS (CDN build in index; local build in chunks), React Router 7
- Firebase: Auth (phone OTP), Firestore, Storage
- Motion (animation) via jsDelivr; Google Fonts; `@remotion` + WebCodecs for in-browser export
- Build chunks named `HomePage-*.js`, `EditorPage.js`, `Editor-*.js`, `PricingPage-*.js` — code-split per route

**Backend / infra**
- **Cloudflare** (DNS, CDN, WAF, email obfuscation, Cloudflare Insights RUM, bot score)
- **Cloudflare R2** as media CDN (`media.hyproai.com`, CORS restricted to hyproai.com) — media asset store
- **Express** API on the editor host (nginx origin behind Cloudflare)
- **AWS Lambda** "export renderer" (server-side render path alongside client-side Remotion)
- **Google Cloud Speech-to-Text Chirp** for transcription
- **Cashfree** payments (UPI/autopay/e-mandate)
- WordPress on `blog.hyproai.com` (separate origin, currently 522)
- Google Workspace email (MX), Hostinger registrar, Let's Encrypt certs
- Analytics: GA4 (`G-M29YCKT3YW` landing / `G-4BF9GCKRLE` app), GTM (`GTM-5WCVT84D`, `GTM-T83R2ZQ7`), Meta Pixel (`1004598929138250`, `1640792224309509`), Web3Forms contact
- **SEO/CRAWL stance:** robots.txt blocks all major AI-training crawlers (GPTBot, ClaudeBot, PerplexityBot, CCBot, Google-Extended, etc.) + `Content-Signal: ai-train=no`, ai-index=no, ai-answer=no — signals a deliberate anti-AI-scraping posture (relevant to OSS clone positioning)

**Key API endpoints observed**
`/api/pipeline-config`, `/api/generate-timeline-data`, `/api/upload-timeline-assets`, `/api/hypro/client-fallback|client-renditions|client-complete`, `/api/usage/consume`, `/api/render/release-abandoned`, `/api/storage/audit|cleanup-expired`, `/api/team*`, `/api/announcements/current`, `/api/desktop/releases/latest/manifest.json`, `/api/influencer/redeem`, `/api/delete-project-media`, `/api/profile/reconcile`, `/api/client-error`, `/api/admin/*` (Pipeline, CostMonitoring, Influencers, PromoCodes, Presets, Users, Lifecycle, Support)

**Subdomains (crt.sh):** `*.hyproai.com`, `editor.` (app), `beta.` (mobile/PWA), `media.` (R2 CDN), `blog.` (+ `www.blog.`, TLS fail), `admintharunnizampanel.` (internal Vite "hypro-admin" panel on Google Frontend — likely employee/admin tooling)

---

## (e) Public Workflow Descriptions (from 9 in-app tutorials + landing)

1. **Getting Started** — Create account → first project → upload (MP4/MOV/WebM/MP3/WAV, ≤10 min) → pick language (33 langs / Auto-Detect) + output mode (Native/Romanized/English) → generate captions (word timestamps, cancelable job) → review on timeline → export MP4
2. **Editing Your Timeline** — Drag-drop clips; word-level transcript select; split/trim/snap; keyboard shortcuts; autosave
3. **Silence Remover** — Smart pass (auto) or Custom (set min pause seconds); silent ranges cut or replaced with background
4. **Retake Remover** — Choose setting → AI finds retakes/flubs → keeps best take, deletes bad lines
5. **Audio Enhance** — Upload voice + music → Rebalance/ducking w/ loudness analysis → balanced output
6. **Caption Styles & Fonts** — Pick style preset → animate (slide-up/glow/word-by-word) → customize → save custom preset (gated) → export
7. **Auto Zoom & Filters** — Enable → caption-driven zoom punches + filters
8. **Exporting to Premiere Pro** — Choose settings → download ZIP (project file + media + captions + transparent caption overlay video) → import → captions editable
9. **Exporting to Resolve** — Same flow as Premiere (Resolve-compatible XML)

**Export formats:** MP4 (H.264, plan-capped resolution), SRT, VTT, TXT, Premiere XML, Resolve XML, FCPXML, AE EDL. Export history in Downloads page with status (exporting/complete/failed), download link + expiry.

**Caption rules in-product:** max length 35 chars default, min duration 0.5s, gap 0, Single Word / Double Line modes, safe-zone guard.

---

## (f) OSS-Clone Gaps / Opportunities

### What is genuinely hard to replicate (moats)
1. **Chirp-grade multilingual ASR + romanization** — the 33-language, romanized-caption pipeline (Hinglish/Thanglish/etc.) is the core differentiator. An OSS clone needs a self-hosted ASR (Whisper large-v3 / faster-whisper / NeMo) + a romanization layer; quality parity on Indian languages is the real work.
2. **Silence/Retake removal quality** — heuristic silence cutting is easy; "keep the best take, drop the flub" requires word-level VAD + semantic/quality scoring (LLM or audio-quality classifier).
3. **Export-to-NLE fidelity** — Premiere/Resolve XML with 5 correctly-timed layers (video, b-roll, music, voice, captions) + transparent caption overlay video is fiddly and rarely done well in OSS.
4. **In-browser 4K export** — Remotion + WebCodecs client render avoids their Lambda cost; an OSS clone can actually *beat* them here (no cloud render fee, no upload-wait) if you ship WASM encoders.
5. **Distribution/brand** — 10,000+ creators, 4.9 rating, Indian creator community, founders with 700K IG following. Not a code moat but a real one.

### What an OSS clone can match or beat cheaply
| HyproAI | OSS path |
|---|---|
| Firebase auth/storage/firestore | Self-host Supabase / Appwrite / Directus |
| Cloudflare R2 media | MinIO / Cloudflare R2 (same) / local S3 |
| Google Chirp ASR | faster-whisper / whisper.cpp / NeMo Parakeet / MMS |
| Cashfree payments | Stripe / Lemon Squeezy / Polar (or leave payments out) |
| Cloudflare + Express | Any reverse proxy + FastAPI/Hono |
| Remotion + WebCodecs | Same (Remotion is source-available; WebCodecs is native) |
| AWS Lambda export renderer | Skip — client-side only, lower cost |
| Google Fonts / Pexels stock | Same APIs (both free tiers) |
| GA4/GTM/Meta Pixel | Plausible / Umami (privacy-respecting, a differentiator) |
| WordPress blog (SEO spam) | Static Astro blog, no spam |

### Gaps / underserved angles for an OSS competitor
1. **Music & SFX library** — competitors (Supercaptions et al.) explicitly cite HyproAI as lacking a music/SFX library. An OSS clone with a bundled CC0/Free Music Archive library wins a real feature gap.
2. **Pay-as-you-go pricing** — HyproAI has no true PAYG (minutes packs only, ₹10/min). A usage-based OSS-hosted option (or self-host = free) undercuts it.
3. **Offline / local-first** — HyproAI requires cloud upload (R2) for every project. A fully-local desktop OSS (whisper.cpp + FFmpeg) serves privacy-conscious editors and removes the "your video goes to the cloud" objection.
4. **NLE-native plugin** — Ship as a Premiere/Resolve/FOA plugin instead of a web app; different distribution surface.
5. **Deterministic, version-controlled edits** — edit-as-code (EDL/FCPXML in git) vs. their opaque timeline JSON.
6. **No AI-training-blocked content** — their robots.txt aggressively blocks AI crawlers; an OSS project with open docs/README is more agent-discoverable.
7. **The "daily pass" gap** — ₹89/day is a good conversion wedge; an OSS clone can offer a free self-host tier with no minute cap (compute-bound only).
8. **Multicam/Podcast are gated high (Creator/Agency)** — an OSS clone can ship them ungated.

### Suggested OSS feature parity checklist (MVP → full)
- MVP: upload → ASR (whisper) → word timestamps → romanize (Indian langs) → auto-captions + SRT/VTT/TXT → silence removal → timeline edit → MP4 export (WebCodecs) → no watermark
- V1: retake remover, audio enhance (ducking), caption styles/animations, Pexels stock, NLE XML export (Premiere + Resolve)
- V2: podcast + multicam editors, custom fonts/presets, Auto Zoom/Filters, Auto SFX, desktop app (Tauri)
- Differentiators to lead with: **fully local/offline mode**, **music+SFX library**, **edit-as-code export**, **no account required**

### Risks / watch-outs
- "World's first silence remover" and "3x faster, 2x cheaper than Descript" are unverified marketing claims — do not copy them into OSS copy.
- Company is <1 year old (inc. Dec 2025), single legal entity in TN India, blog origin currently down — durability of the incumbent is uncertain; timing favors a clone.
- Trademark/name: "HyproAI" is their brand — the OSS project should use its own name; do not fork their name, logo, or copy.
- Their `admintharunnizampanel` subdomain suggests active internal tooling — the product is under active development, so feature parity will drift; track their bundle chunks (`PricingPage-*.js`, `featureGate-*.js`) for changes.
