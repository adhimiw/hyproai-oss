# 08 — Export to NLEs: generating multi-layer, editor-importable project/timeline files

Research date: 2026-11-04. All findings verified by web search + local experiments (OTIO was
installed and run in a scratch venv; outputs inspected).

**Scope:** export HyproAI's 5 layers (video A-roll, b-roll, music, voice/dialog, captions) as a
multi-track timeline into **Premiere Pro, DaVinci Resolve, After Effects** (+ Final Cut Pro as a
bonus), using only local/offline, pip-installable, permissively-licensed tooling.

---

## 0. Executive answer (details in §7 RECOMMENDED PICK)

| Target | Importable format | How we generate it | Layers preserved? |
|---|---|---|---|
| Premiere Pro | **FCP7 XML (xmeml v4/v5)** — File ▸ Import | `pip install opentimelineio otio-fcp-adapter` → `write_to_file(..., "fcp_xml")` | ✅ all video + audio tracks |
| DaVinci Resolve (FREE) | **FCP7 XML** (also FCPXML, AAF, EDL, OTIO) — File ▸ Import ▸ Timeline | same OTIO `fcp_xml` writer (or raw `.otio`) | ✅ all tracks |
| After Effects | **no native FCP7 XML/EDL** → route: FCP7 XML ▸ Premiere ▸ save `.prproj` ▸ AE import; *or* ship a generated `.jsx` script; AAF import = Windows-only | OTIO for XML + a small hand-written ExtendScript emitter | ✅ (via prproj) or ✅ (via jsx) |
| Final Cut Pro (bonus) | **FCPXML** — File ▸ Import ▸ XML | `pip install otio-fcpx-xml-adapter` (writes FCPXML 1.8) | ✅ |
| Captions (all) | **`.srt` sidecar** (FCP7 XML has no subtitle track concept) | hand-rolled writer or `pip install srt` | ✅ as its own file |

Fallback if XML generation proves fragile in QA: **one CMX3600 EDL per track (`style="premiere"`) + SRT + JSON cutlist** — all three trivially generated, all three importable everywhere.

---

## 1. What each NLE actually imports

### 1.1 Premiere Pro
- **Imports:** FCP7 XML (`xmeml`, versions 4/5/6 — Premiere speaks a slightly-modified xmeml based on FCP Classic XML v6 with Adobe-specific tags), **AAF**, **CMX3600 EDL**, `.prproj`, AEP/AEPX, batch lists (CSV/TXT).
  Source: Adobe help "Import XML project files from Final Cut Pro 7" — Premiere "retains the sequence markers, sequence settings, **track layout**, locked tracks, and sequence timecode start points".
- **Does NOT import:** `.fcpxml` (FCP X XML) — Adobe's own doc says use XtoCC to convert first.
- **Captions:** File ▸ Import a `.srt` → it appears as a captions item; drag above video tracks → Premiere creates a **caption track** with cues at their timecodes. (Verified across multiple sources: Adobe/Rev/mydubly/CaptionBolt.)
- **Track naming caveat:** Premiere numbers tracks V1…/A1… and does **not** carry custom track names from FCP7 XML (per CutConvert's format notes). Layers are preserved as *positions*, not names → put layer semantics in clip names too.
- FCP7 XML is the lingua franca: *"The dominant user of xmeml is Premiere Pro CC"* (fcp.cafe developer case study).

### 1.2 DaVinci Resolve (free)
- **Imports (File ▸ Import ▸ Timeline):** FCP7 XML, AAF, EDL, FCPXML (1.8–1.10), DRT, **and `.otio` since v17** (Free and Studio — pulseedit.com; BMD forum confirms). Resolve's user guide: *"XML is one of the most straightforward methods of bringing in edits with as many video tracks as you need"*.
- **Captions:** File ▸ Import ▸ Subtitle… (`.srt`, `.vtt`, `.xml`, `.ttml`) → subtitle clip in Media Pool → drag onto timeline → Resolve **creates a subtitle track** automatically, or right-click ▸ *Insert Selected Subtitles to Timeline Using Timecode* for exact placement. (BMD Resolve manual part1280.)
- **Scripting API (Python):** ⚠️ **no longer available in the free version.** Blackmagic moved Python scripting to **Studio** (BMD forum thread "v21.1 Advanced scripting now requires DaVinci Resolve Studio"; Puget Systems, 2026-09-10: *"With DaVinci Resolve v21.1, Blackmagic has removed scripting support from the free version"*; `dvr` docs say external scripting has been Studio-only since v19.1). ⇒ **Do not build the export path on Resolve scripting.** File-based import works in the free build and is the portable path. (Scripting remains a nice optional "one-click import" for Studio users: `MediaPool.ImportTimelineFromFile(path, {"timelineName": ..., "importSourceClips": False, ...})`.)
- Import gotchas (from davinci-resolve-mcp headless-edit-loop guide): `timelineName` must be unique per import or `ImportTimelineFromFile` silently returns None; Resolve honors the **internal sequence name** inside the XML over the option; pass `importSourceClips: False` to avoid duplicating media.

### 1.3 After Effects — the hard one
- **No native FCP7 XML import anymore.** Adobe removed XML import from AE in the **17.0.5** update (Nov 2020); official recommendation became: *import the XML into Premiere Pro, save, then open that `.prproj` in After Effects* (Larry Jordan, citing Adobe). The "Pro Import After Effects" plug-in (Automatic Duck, bundled free) that used to import **AAF/OMF/FCP7 XML/Motion 4** is **removed/broken in current versions** — Adobe Community: *"We really need Pro Import back to work with AAF files. I know it was removed"*; Reddit r/AfterEffects: *"Pro import AE no longer appears for XML files … Adobe broke this functionality."* Adobe's help pages still document it — treat as stale; verify at build time, don't depend on it.
- **What AE *does* import (current Adobe supported-formats page):**
  - `.prproj` (Premiere) — **Import and Export** → the documented bridge: our FCP7 XML ▸ Premiere ▸ Save ▸ AE File ▸ Import ▸ Adobe Premiere Project. AE converts Premiere clips to layers "based on the arrangement of clips in the Premiere Timeline panel … from the bottom up and from left to right", preserving trims, markers, transitions, speed.
  - `.aaf` — **Import only, Windows only**.
  - `.aepx` (XML project) — text-based but ~90% hex-encoded binary; not realistically generatable.
  - Data files: JSON/CSV/TSV (as footage, not timelines).
- **Does AE import EDL?** No (not in the formats list; community answer: "Import the EDL into Premiere, save, import into AE"). Third-party aescripts exist but are paid.
- **Captions:** AE has no SRT import; caption tracks arriving via `.prproj` are best-effort. Plan for captions to be re-created or handled via our JSX route (text layers).
- **Practical AE paths we can ship:**
  1. **`.prproj` route** (zero extra code): FCP7 XML → Premiere → AE. Needs Premiere in the loop.
  2. **Generated ExtendScript (`.jsx`)**: AE's scripting API *can* build a comp from scratch (`project.items.addComp`, `comp.layers.add(footage)`, `layer.startTime/inPoint/outPoint`). We emit `hypro_build.jsx` + our JSON; user runs File ▸ Scripts ▸ Run Script File. ~100–200 lines, fully under our control, works on Mac+Win, no Premiere needed. (Pattern references: TerminalSkills AE skill, Plainly/AE automation guides.)
  3. **AAF on Windows** (OTIO AAF writer, see §2.3).

### 1.4 Final Cut Pro (bonus)
- Imports **FCPXML only** (not FCP7 XML, not AAF, not EDL). `otio-fcpx-xml-adapter` writes FCPXML **1.8** (a bit dated — OTIO discussion #1470 — but importable by FCP 10.4+).
- Captions: FCP imports SRT (File ▸ Import ▸ Captions); for *embedded* caption titles use `hysmichael/srt_fcpxml_converter` (SRT → FCPXML titles; 39★, **no LICENSE file** — treat as reference code, don't vendor verbatim).

---

## 2. Generator candidates (detail cards)

### 2.1 ⭐ OpenTimelineIO + `otio-fcp-adapter` (FCP7 XML) — PRIMARY
| | |
|---|---|
| Repo | https://github.com/AcademySoftwareFoundation/OpenTimelineIO + https://github.com/OpenTimelineIO/otio-fcp-adapter |
| License | **Apache-2.0** (both) |
| Stars / maintenance | OTIO **2,055★**, pushed 2026-10-31, v0.18.1 (Nov 2025); otio-fcp-adapter **11★**, pushed 2026-04, PyPI 1.0.0. ASWF incubating, very active. |
| Formats | read+write **FCP7 XML (xmeml)**, CMX3600 EDL, AAF (via otio-aaf-adapter), FCPXML (via otio-fcpx-xml-adapter), native `.otio/.otiod/.otioz`, ALE, etc. |
| Install | `pip install opentimelineio otio-fcp-adapter otio-cmx3600-adapter otio-aaf-adapter` (granular) **or** `pip install opentimelineio opentimelineio-plugins` (meta pkg, v0.18.1 on PyPI, pulls all adapters). Since 0.17 adapters left the core package. |
| Multi-track | ✅ FCP7 XML feature matrix: Multiple Video Tracks ✔, Audio Tracks ✔, Gaps ✔, Markers ✔ (no transitions/effects — irrelevant for a cut list). |

**VERIFIED LOCALLY** (OTIO 0.18.1 + otio-fcp-adapter, Python 3.14 venv): built a 4-track timeline
(V1 = 3 jump-cut segments, V2 = b-roll + gaps, A1 = music, A2 = voice) and exported:

```
wrote FCP7 XML  →  well-formed <xmeml version="4">
video track count: 2 | audio track count: 2 | clipitems: 7
jump-cut segments = 3 separate <clipitem> on one <track>, all sharing <file id="file-1">  ✅
<in>/<out>/<start>/<end> present on every clipitem (7/7)                                  ✅
gaps = simply no clipitem (implicit), no filler objects needed                            ✅
```

Minimal usage snippet (the real thing we'd ship):

```python
# pip install opentimelineio otio-fcp-adapter
import opentimelineio as otio
RATE = 30
def rt(sec): return otio.opentime.RationalTime(int(sec * RATE), RATE)

def clip(name, path, src_in, dur):
    ref = otio.schema.ExternalReference(
        target_url="file://" + path,
        available_range=otio.opentime.TimeRange(rt(0), rt(3600)))
    return otio.schema.Clip(name=name, media_reference=ref,
        source_range=otio.opentime.TimeRange(rt(src_in), rt(dur)))

tl = otio.schema.Timeline("HyproAI_Export")
def track(name, kind, items):
    t = otio.schema.Track(name=name, kind=kind)
    for i in items: t.append(i)
    tl.tracks.append(t)

def gap(sec):
    return otio.schema.Gap(source_range=otio.opentime.TimeRange(rt(0), rt(sec)))

track("V1", otio.schema.TrackKind.Video, [          # A-roll: jump cuts = separate clips
    clip("aroll_1", "/m/aroll.mp4", 10.0, 3.0),
    clip("aroll_2", "/m/aroll.mp4", 20.0, 2.0)])
track("V2", otio.schema.TrackKind.Video, [gap(2.0), clip("broll1", "/m/b1.mp4", 5.0, 3.0)])
track("A1", otio.schema.TrackKind.Audio, [clip("music", "/m/music.mp3", 0.0, 10.0)])
track("A2", otio.schema.TrackKind.Audio, [gap(1.0), clip("voice", "/m/vo.wav", 0.0, 8.0)])

otio.adapters.write_to_file(tl, "export.xml")   # FCP7 XML, target extensions auto-resolve
# → Premiere: File ▸ Import ▸ export.xml ; Resolve: File ▸ Import ▸ Timeline
```

Pros: single internal timeline model → all formats; Apache-2.0; battle-tested by Kdenlive/Resolve/VFX
pipelines; gaps/markers/rational time handled; the same object also writes EDL/AAF/FCPXML/`.otio`.
Cons: adapter writes **no `<link>`/`<sourcetrack>` elements** (A/V pairs arrive unlinked in Premiere —
put a-roll audio on an explicit audio track with identical in/out and let the editor relink, or accept
unlinked); writes a duplicated `<rate>` block per clipitem (harmless in practice, but smoke-test each
NLE); no transitions/effects (we don't need them); pre-1.0 versioning.

### 2.2 OpenTimelineIO CMX3600 EDL adapter (fallback EDLs)
| | |
|---|---|
| Repo | https://github.com/OpenTimelineIO/otio-cmx3600-adapter |
| License | Apache-2.0 · 10★ · pushed 2025-12 · PyPI 1.0.0 (`otio-cmx3600-adapter`) |
| Formats | CMX3600 EDL read/write, styles: `avid` (default), `nucoda`, **`premiere`** |
| Multi-track | ❌ EDL = **single video track per file** (feature matrix: Multiple Video Tracks ✖) → emit **one EDL per track**. Audio events supported. |

**VERIFIED LOCALLY**: exported V1/V2 EDLs —
```
TITLE: V1
001  arollmp4 V     C        00:00:10:00 00:00:13:00 00:00:00:00 00:00:03:00
* FROM CLIP NAME:  aroll_cut0
002  arollmp4 V     C        00:00:20:00 00:00:22:00 00:00:03:00 00:00:05:00
...
```
Usage: `otio.adapters.write_to_string(track_timeline, adapter_name="cmx_3600", style="premiere")`.
Pros: dead simple, universally importable (Premiere + Resolve + Avid), zero ambiguity about track order
(you label each file `V1.edl`, `V2.edl`…). Cons: single track/file, reel names truncated to 8 chars,
no gaps (placement is implied by record TC), no captions, no effect data.

### 2.3 OpenTimelineIO AAF adapter (+ pyaaf2)
| | |
|---|---|
| Repo | https://github.com/OpenTimelineIO/otio-aaf-adapter · https://github.com/markreidvfx/pyaaf2 |
| License | **Apache-2.0** (adapter) · **MIT** (pyaaf2) |
| Stars / maintenance | adapter 23★, pushed 2026-10-22, PyPI **2.0.0**; pyaaf2 **174★**, PyPI 1.7.1, last push 2024-11 (stable but slow-moving) |
| Formats | AAF read+write. Feature matrix: Multiple Video Tracks ✔ read/write, Gaps ✔, Markers ✔, Nesting ✔, Transitions ✔; effects ✖; **linear speed write ✖** |
| Install | `pip install opentimelineio otio-aaf-adapter` (pulls pyaaf2; wheel is py3-none-any, zero compiled deps) |

**VERIFIED LOCALLY**: default write fails with `Cannot find mob ID for clip …`; adding the adapter
option `use_empty_mob_ids=True` succeeds and round-trips:

```python
otio.adapters.write_to_file(tl, "export.aaf", adapter_name="AAF", use_empty_mob_ids=True)
# 520,192 bytes; read back: V1(3 clips), V2(4), A1(1), audio "Sequence"(2)
```

Pros: AAF is the professional interchange (Premiere ✔, Resolve ✔, Avid ✔, **AE on Windows ✔**),
explicit A/V linking and track layout. Cons: needs the `use_empty_mob_ids` workaround for
externally-referenced media (empty mob IDs can hurt relinking); audio track naming quirk on
round-trip (A2 came back as "Sequence"); no speed effects on write; Windows-only for AE.

### 2.4 `otio-fcpx-xml-adapter` (Final Cut Pro)
https://github.com/OpenTimelineIO/otio-fcpx-xml-adapter · Apache-2.0 · PyPI `otio-fcpx-xml-adapter` 1.0.0.
Writes FCPXML **1.8** (multi-track/connected-clip model ✔). Use only if we care about FCP users;
Resolve also reads FCPXML (1.9/1.10 exported by Resolve itself; 1.8 files generally import).

### 2.5 ChatOctopus/timeline (Node/TypeScript)
| | |
|---|---|
| Repo | https://github.com/ChatOctopus/timeline · npm `@chatoctopus/timeline` |
| License | **MIT** · 8★ · pushed 2026-06-24 (young) |
| Formats | read+write **xmeml v5 (Premiere & Resolve)**, FCPXML 1.8 (FCP), OTIO; CLI `npx @chatoctopus/timeline convert` |
| Usage | `exportTimeline(timeline, "premiere" | "resolve" | "fcpx" | "otio")` from a plain `{tracks: [{kind:"video", name:"V1", items:[...]}]}` object; frame-accurate rational time; `buildTimelineFromFiles()` probes media with ffprobe. |

Pros: purpose-built "our internal model → Premiere/Resolve XML", clean TS API, MIT, handles both
targets. Cons: tiny project (8★), JS not Python (extra runtime), ESM-only, container elements
(`<sequence>`-in-clip) not parsed yet.

### 2.6 CutSmith timeline bridge (CapCut → Premiere) — ⚠️ gone
Was: https://github.com/CutSmithIO/cutsmith-timeline-bridge — MIT, Python ≥3.10 **stdlib only**,
v0.4.1, generated FCP7 XML with confirmed multi-track (V1/V2/V3, A1/A2), master-clip `<clipitem>`
reconstruction, `timeremap` speed filters, media collection + path rewriting, and a companion
`export-srt` for Premiere Captions.
**Status 2026-11-04: repo returns 404** (deleted or made private; README still in search indexes).
⇒ Use as *evidence the approach works* and as a design reference (its report/relink-guide UX is worth
copying), but not as a dependency.

### 2.7 FilmCraft (Rust NLE) — reference implementation
https://github.com/storytold/filmcraft · **Apache-2.0 OR MIT** · **17,458★** · pushed 2026-11-04 (very active).
`crates/interchange/src/fcp7.rs` (1,477 lines) is a complete FCP7 xmeml **writer+reader**: video/audio
tracks with enable/lock, `pproTicksIn/Out`, nested sequences, generators, transitions, Basic Motion /
Opacity / Time Remap / Audio Levels with keyframes. Also writes FCPXML 1.9–1.11, OTIO, CMX3600
("one EDL per track"), AAF (Edit Protocol), OMF.
⇒ Not pip-installable, but **the best open-source reference for a hand-rolled xmeml writer** if we
ever outgrow the OTIO adapter (e.g., need track-level metadata, transitions, or Premiere-specific
tags). Note their own docs confirm FCP7 XML is the format "Premiere and DaVinci Resolve already
exchange".

### 2.8 xmeml-specific Python libs
- `linwownil/xmeml` — 4★, MIT, last push 2023. "Automated XMEML file generation" — small generator, low activity.
- `ccnmtl/xmeml` (Columbia CompilED) — 23★, **archived, last commit 2010**, license NOASSERTION, Py2-era. Read/clip-merge oriented. Skip.
- Hand-rolling xmeml from scratch: viable (the Qiita article documents a *minimal* FCP7 XML Resolve
  accepts: sequence + video/audio tracks + clipitems with pathurl/rate/in/out/start/end, plus a `.srt`
  for captions — a clean worked example), but you inherit all compatibility edge cases yourself.
  Only worth it if OTIO's adapter blocks a hard requirement.

### 2.9 Resolve scripting wrappers (optional, Studio-only)
- `dvr` — https://github.com/mhadifilms/dvr · MIT · 15★ · `pip install dvr` · typed wrapper/CLI/MCP over
  the Resolve API; explicitly requires **Resolve Studio 18.5+** (external scripting is Studio-only).
- Raw API: `MediaPool.ImportTimelineFromFile()`, `timeline.Export(path, resolve.EXPORT_FCP_7_XML, ...)`,
  `CreateTimelineFromClips`, `AddTrack("video"|"audio"|"subtitle")` — powerful for *Studio* users, but
  unusable against the free build since v19.1/v21.1 ⇒ optional "auto-import" feature gated behind a
  detected Studio install, never the primary path.

### 2.10 Others considered
| Name | Notes | Verdict |
|---|---|---|
| `pycmx` (PyPI 1.5.1) | CMX3600 **parser** only | ❌ no writer |
| `edl` (PyPI 0.1.11) | reader-oriented, **license UNKNOWN** | ❌ avoid (license constraint) |
| `srt` (PyPI 3.5.3, MIT) | SRT read/write | ✅ optional — SRT is 20 lines to emit by hand |
| MLT XML (Kdenlive/Shotcut) | rich multi-track, but Adobe/Resolve can't read MLT | ❌ not portable |
| `.prproj` generation | binary/undocumented; AE/Premiere import it but nothing OSS writes it | ❌ |
| `.aep`/`.aepx` generation | hex-encoded binary payload | ❌ |
| Automatic Duck Ximport AE / Pro Import | paid or removed (see §1.3) | ❌ |
| Premiere ExtendScript | API is largely read-only for timeline placement | ❌ |

---

## 3. Real OSS projects that already export to Premiere/Resolve (proof the approach works)

1. **FilmCraft** (17.4k★) — full read/write interchange crate: xmeml, FCPXML, OTIO, EDL, AAF, OMF.
2. **CutSmith** (gone, MIT) — CapCut → Premiere FCP7 XML + SRT + collected media; documented
   multi-track and speed preservation; validated against Premiere 2024/2025.
3. **ChatOctopus/timeline** (8★, MIT) — model → `premiere`/`resolve`/`fcpx`/`otio`.
4. **OTIO ecosystem** (2k★) — adapters used by Kdenlive, VFX houses, and tools like
   Sequence (sequence.film exports OTIO for Resolve), Eddie AI (OTIO export), CutSmith-class tools.
5. **Qiita article (2025-07)** — minimal Python FCP7-XML generator + `.srt` imported into Resolve
   end-to-end (timeline + subtitles); documents Resolve's minimum accepted XML.
6. **ChatCut / CutConvert** (commercial, but their public format docs confirm): FCP7 xmeml is the
   interchange for both Premiere and Resolve; captions ride as separate captions/Text-generator clips.
7. **davinci-resolve-mcp** (Gursky) — verified loop of FCP7 XML export → mutate → re-import in Resolve;
   documents the unique-name/internal-name gotchas.

---

## 4. Layer/track mapping for HyproAI's 5 layers

### 4.1 Track layout (FCP7 XML & AAF)

```
<xmeml version="4"> → <project> → <sequence>
  <media>
    <video>            ← FIRST <track> = V1 (bottom layer)
      <track>  V1  : A-roll jump-cut segments (each cut = its own <clipitem>, same <file id>)
      <track>  V2  : b-roll
      (V3+ : graphics/text if ever needed)
    </video>
    <audio>            ← FIRST <track> = A1
      <track>  A1  : music (full-length or per-segment)
      <track>  A2  : voice/dialog (VO or cleaned a-roll audio)
      (A3  : a-roll production audio if separate)
    </audio>
  </media>
```

- **Track order verified**: in a real Premiere-exported sample
  (`otio-fcp-adapter/tests/sample_data/premiere_example.xml`), the *first* `<track>` under `<video>`
  contains clipitems whose `<link>` blocks say `<mediatype>video</mediatype><trackindex>1</trackindex>`,
  and the second `<track>` says `trackindex 2`. ⇒ **first track element = V1**, and OTIO's writer
  emits `stack` children in order (child 0 = bottom = V1). Both OTIO and ChatOctopus write in this
  order — keep our internal model "index 0 = V1/A1" too.
- **Jump cuts**: N segments of the same source = N `<clipitem>` entries on one `<track>`, sharing one
  `<file id="file-1">`, each with its own `<in>/<out>` (source) and `<start>/<end>` (timeline).
  Verified in our generated XML: 3 clipitems → `file-1`.
- **Gaps**: no element needed — absence of a clipitem between `start` values is the gap (OTIO `Gap`
  objects simply produce nothing).
- **Subclips vs clips**: for a cut list we emit plain `clipitem`s pointing at master `<file>`s
  (`ismasterclip` optional). Only use `<subclip>`/`subclipinfo` if we want named subclips in the
  project panel (CutSmith rebuilds master `clipitem`s at the xmeml root so the Project panel shows
  real source clips — nice-to-have; OTIO's adapter doesn't do this).
- **A/V linking**: our model should place sync a-roll audio on A2 with *identical* in/out as V1 so
  even unlinked playback is frame-synced. (OTIO emits no `<link>` elements; AAF does carry links.)
- **Track names**: not carried by Premiere from FCP7 XML → also embed layer meaning in clip names
  (`V2_broll_…`).
- **Captions**: FCP7 XML has **no subtitle/caption track** — captions MUST be a separate `.srt`
  sidecar (both Premiere and Resolve import it as a real caption/subtitle track; FCP too).
  If we want *styled* captions inside FCP, `srt_fcpxml_converter` embeds them as FCPXML titles.

### 4.2 Caption import specifics (all three targets + FCP)
| App | Import | Result |
|---|---|---|
| Premiere | File ▸ Import ▸ `.srt` → drag to timeline | **caption track**, cues at timecodes, styleable |
| Resolve | File ▸ Import ▸ Subtitle… → drag above video / "Insert Using Timecode" | **subtitle track**, per-track styling in Inspector |
| Final Cut Pro | File ▸ Import ▸ Captions (SRT) | caption role track |
| After Effects | — | no SRT import; use JSX route to create text layers, or accept manual step |

Timeline must start at `00:00:00:00` or cues shift by the start TC (common pitfall, noted by
multiple sources).

---

## 5. Format capability matrix (what survives)

| Capability | FCP7 XML | AAF | CMX3600 EDL | FCPXML | OTIO(.otio) | SRT |
|---|---|---|---|---|---|---|
| Premiere import | ✅ | ✅ | ✅ | ❌ (needs XtoCC) | ❌ | ✅ (captions) |
| Resolve import | ✅ | ✅ | ✅ | ✅ | ✅ (v17+) | ✅ (subtitles) |
| After Effects import | ❌ (via Premiere .prproj) | ✅ Windows only | ❌ | ❌ | ❌ | ❌ |
| Final Cut Pro import | ❌ | ❌ | ❌ | ✅ | ❌ | ✅ |
| Multiple video tracks | ✅ | ✅ | ❌ (1/file) | ✅ | ✅ | n/a |
| Multiple audio tracks | ✅ | ✅ | ⚠️ (events w/ A1/A2 flags) | ✅ | ✅ | n/a |
| Gaps | implicit | ✅ | implied by record TC | ✅ | ✅ | n/a |
| A/V link metadata | ⚠️ (OTIO omits `<link>`) | ✅ | ❌ | ✅ | ✅ | n/a |
| Captions/subtitles | ❌ | ❌ | ❌ | ✅ (titles) | ❌ | ✅ |
| Track names | ❌ (Premiere) | ✅ | ❌ | ✅ | ✅ | n/a |
| Generator (offline/no media) | ✅ pathurl | ⚠️ needs mob id | ⚠️ reel names | ✅ | ✅ | — |

---

## 6. Fallback: minimum viable export set (no XML at all)

If FCP7 XML ever breaks in a target version, this set still delivers "separate editable layers":

```
export/
├── timeline.fcp.xml        # primary: OTIO fcp_xml (4-track)
├── tracks/V1.edl           # fallback: one CMX3600 per track, style="premiere"
├── tracks/V2.edl
├── tracks/A1.edl
├── tracks/A2.edl
├── captions.srt            # captions for Premiere + Resolve + FCP + YouTube
└── cutlist.json            # machine+human readable ground truth (always ship this)
```

`cutlist.json` (never fails, never needs an NLE) — the source of truth we can regenerate any format from:

```json
{
  "version": 1, "fps": 30, "resolution": [1080, 1920], "start_tc": "00:00:00:00",
  "tracks": [
    {"id": "V1", "kind": "video", "role": "aroll", "clips": [
      {"media": "/path/aroll.mp4", "src_in": 300, "src_out": 390, "rec_in": 0,   "rec_out": 90},
      {"media": "/path/aroll.mp4", "src_in": 600, "src_out": 660, "rec_in": 90,  "rec_out": 150}]},
    {"id": "V2", "kind": "video", "role": "broll", "clips": [{"media": "/path/b1.mp4", "src_in": 150, "src_out": 240, "rec_in": 60, "rec_out": 150}]},
    {"id": "A1", "kind": "audio", "role": "music", "clips": [{"media": "/path/music.mp3", "src_in": 0, "src_out": 300, "rec_in": 0, "rec_out": 300}]},
    {"id": "A2", "kind": "audio", "role": "voice", "clips": [{"media": "/path/vo.wav", "src_in": 0, "src_out": 240, "rec_in": 30, "rec_out": 270}]}
  ]
}
```

EDL generation: build a one-track OTIO Timeline per track → `write_to_string(..., adapter_name="cmx_3600", style="premiere")` (verified output above).
Everything else (JSON, SRT) is stdlib-only.

---

## 7. RECOMMENDED PICK

### Export set shipped per project (one folder, five files)

```
hyproai_export_<name>/
├── <name>.xml          # FCP7 XML (xmeml v4) — THE deliverable for Premiere + Resolve
├── <name>.srt          # captions/subtitles (own layer, imported separately)
├── <name>.otio         # same timeline, open format (Resolve v17+ import, future-proofing)
├── <name>.jsx          # After Effects: ExtendScript that rebuilds the comp from cutlist.json
├── cutlist.json        # ground truth / regression fixture / fallback source
└── tracks/*.edl        # optional belt-and-braces: one CMX3600 (style="premiere") per track
```

### Tool choices (all pip-installable, offline, permissive)

| Purpose | Package | License |
|---|---|---|
| Timeline model + FCP7 XML writer | `opentimelineio` + `otio-fcp-adapter` | Apache-2.0 |
| EDL fallback | `otio-cmx3600-adapter` | Apache-2.0 |
| AAF (Windows AE / Pro-style handoff) | `otio-aaf-adapter` (+`pyaaf2`) — call with `use_empty_mob_ids=True` | Apache-2.0 / MIT |
| FCP users (optional) | `otio-fcpx-xml-adapter` (FCPXML 1.8) | Apache-2.0 |
| Captions | hand-written SRT emitter (stdlib) or `srt` | MIT |

Install line: `pip install opentimelineio otio-fcp-adapter otio-cmx3600-adapter otio-aaf-adapter`

### Per-editor story
1. **Premiere Pro** — `File ▸ Import ▸ <name>.xml`. Arrives as a sequence with **V1 = A-roll
   (jump cuts as separate clips), V2 = b-roll, A1 = music, A2 = voice** (verified: 4 tracks / 7
   clipitems generated locally). Then `File ▸ Import ▸ <name>.srt` → caption track. Document the two
   known gaps: track names aren't carried (V1/A1 only) and clips may arrive unlinked (link by
   same-file/same-time, or use the AAF variant on Windows which links properly).
2. **DaVinci Resolve (free)** — `File ▸ Import ▸ Timeline ▸ <name>.xml` (or the `.otio`). Same
   4-track structure. Then `File ▸ Import ▸ Subtitle ▸ <name>.srt` → subtitle track
   (*Insert Selected Subtitles to Timeline Using Timecode* for exact placement). **Do not** rely on
   the Python scripting API — Studio-only since v19.1/v21.1; file import works in free.
3. **After Effects** — two supported routes, ship both:
   - **Route A (zero code, needs Premiere):** import `<name>.xml` in Premiere ▸ Save ▸ in AE
     `File ▸ Import ▸ Adobe Premiere Project` — AE maps Premiere clips to layers bottom-up, keeping
     trims/markers/speed.
   - **Route B (our `<name>.jsx`):** AE `File ▸ Scripts ▸ Run Script File` — creates the comp, imports
     media, places every cut segment as a layer with correct `startTime/inPoint/outPoint`, one layer
     per our 5 roles. This is the only route that works without Premiere and without paid plug-ins;
     AAF import is a Windows-only third option (`otio ... adapter_name="AAF", use_empty_mob_ids=True`).
4. **Final Cut Pro (optional extra):** `<name>.fcpxml` via `otio-fcpx-xml-adapter` + `<name>.srt`.

### Fallback (if XML import fails QA on any NLE version)
Ship/keep `tracks/*.edl` (CMX3600, `style="premiere"`, one per layer) + `cutlist.json` + `captions.srt`.
Import path: Premiere/Resolve both ingest EDLs (per track, one at a time), SRT as caption/subtitle
track, and the JSON documents intent for anything EDL drops (gaps are implicit in record TC; reel
names truncate to 8 chars). This set has **zero third-party XML risk** — it's 3 formats, all
trivially generated and stable for 30 years.

### Explicit non-decisions
- Not building our own xmeml writer yet — OTIO's adapter is verified working for the 4-track cut
  case; keep `storytold/filmcraft/crates/interchange/src/fcp7.rs` as the reference if we later need
  transitions, track names, or master-clip reconstruction.
- Not depending on Resolve scripting, Pro Import AE, or `.prproj`/`.aep` generation (removed,
  paid, or closed-format respectively).
- Captions never go inside FCP7 XML — `.srt` sidecar is the universal answer.

---
### Sources (key)
- OTIO docs/adapters: opentimelineio.readthedocs.io (feature matrix), github.com/AcademySoftwareFoundation/OpenTimelineIO, OpenTimelineIO/otio-fcp-adapter, otio-cmx3600-adapter, otio-aaf-adapter, OpenTimelineIO-Plugins.
- Premiere: helpx.adobe.com "Import XML project files from Final Cut Pro 7", "Migrate from Final Cut Pro X"; fcp.cafe "Demystifying Final Cut Pro XMLs"; Adobe supported project formats (AAF, EDL, XML, PRPROJ).
- Resolve: BMD Resolve manual (subtitle import part1280; timeline import), BMD forum "v21.1 Advanced scripting now requires Studio", Puget Systems 2026-09-10, davinci-resolve-mcp headless-edit-loop guide, pulseedit OTIO-import note, Larry Jordan XML articles.
- AE: helpx.adobe.com supported file formats (AAF=import/Windows-only; PRPROJ=import/export), "Importing from After Effects and Premiere", Larry Jordan update "Adobe removed XML import from AE 17.0.5", Adobe Community "Pro-Import still missing on PC", Reddit r/AfterEffects "Pro import AE no longer appears for XML files".
- Projects: github storytold/filmcraft, ChatOctopus/timeline, CutSmithIO/cutsmith-timeline-bridge (404 as of 2026-11-04), linwownil/xmeml, ccnmtl/xmeml (archived), hysmichael/srt_fcpxml_converter, mhadifilms/dvr; PyPI: pyaaf2, pycmx, srt, opentimelineio-plugins.
- Local verification: /tmp/opencode/otio_test.py + out/hypro.xml + out/V1.edl + out/hypro.aaf (OTIO 0.18.1, Python 3.14).
