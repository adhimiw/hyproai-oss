#!/usr/bin/env python3
"""Gemini export-layer probe — can Gemini generate NLE export artifacts?

Four tests per model, all driven from one fixture cutlist:

    srt      caption cues            -> SRT sidecar text        (ms math)
    xml      cutlist JSON            -> FCP7 XML xmeml v4       (frame math)
    edl      cutlist JSON            -> CMX3600                 (timecode math)
    cutlist  keep ranges + dropped   -> cutlist JSON            (structured output)

Ground truth is computed independently here with round(sec * fps); our own
exporter (hypro_core.export.nle.export_nle) runs on the same fixture as a
sanity check of the fixture math, and its artifacts are NOT used as the
expected values (so a bug in our exporter can't mask a bug in Gemini's).

Every prompt includes the format spec plus one worked micro-example, so a
failure means "can't do the arithmetic/structure even when guided", not
"never saw the format".

Usage:
    .venv/bin/python scripts/gemini_export_experiment.py
    .venv/bin/python scripts/gemini_export_experiment.py --models gemini-3.5-flash --tests xml,srt
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

FPS = 30.0
SOURCE = "/videos/janani_reel.mp4"
SOURCE_DURATION = 60.0

# (src_in, src_out) — packed back-to-back on the record timeline.
# Times chosen so no expected frame lands on an exact .5 (avoids
# round-half-to-even ambiguity between us and the model).
# Defined in FRAMES and converted to seconds (rounded to 6 dp) so every
# boundary is an integer frame ±1e-4: round(sec*30) is then unambiguous for
# half-up and banker's rounding alike, and start+dur composition can't drift.
FIXTURE_CLIP_FRAMES = [(63, 193), (293, 422), (549, 660), (904, 1076)]
FIXTURE_CLIPS = [(round(a / FPS, 6), round(b / FPS, 6))
                 for a, b in FIXTURE_CLIP_FRAMES]

# record-timeline caption cues (Thanglish — doubles as a Unicode test)
FIXTURE_CAPTIONS = [
    (0.10, 3.95, "vanakkan friends, inniki namma epdi video cut panradhu nu paakalam"),
    (4.20, 8.10, "first step: silence-ah remove pannanum"),
    (8.70, 12.00, "aduthu retakes-ah correct panniduvom"),
    (12.40, 17.80, "finally captions-ah add panni export pannidalam"),
]

# the source gaps between kept clips (coherent audit trail)
FIXTURE_DROPPED = [
    {"reason": "silence", "start": FIXTURE_CLIPS[0][1], "end": FIXTURE_CLIPS[1][0]},
    {"reason": "retake", "start": FIXTURE_CLIPS[1][1], "end": FIXTURE_CLIPS[2][0]},
    {"reason": "filler", "start": FIXTURE_CLIPS[2][1], "end": FIXTURE_CLIPS[3][0]},
    {"reason": "silence", "start": FIXTURE_CLIPS[3][1], "end": 40.0},
]

# Thanglish caption pool for generated (scale) fixtures
CAPTION_POOL = [
    "vanakkan friends, inniki namma epdi video cut panradhu nu paakalam",
    "first step: silence-ah remove pannanum",
    "aduthu retakes-ah correct panniduvom",
    "finally captions-ah add panni export pannidalam",
    "subscribe pannunga, like share pannunga",
    "adutha video la romba useful tip irukku",
    "camera settings apdiye leave pannidunga",
    "edit pannum pothu undo ctrl z dhaan friend",
]


def set_fixture(clip_frames: list[tuple[int, int]],
                captions: list[tuple[float, float, str]],
                dropped: list[dict]) -> None:
    """Rebind the module-level fixture (used by --scale)."""
    global FIXTURE_CLIP_FRAMES, FIXTURE_CLIPS, FIXTURE_CAPTIONS, FIXTURE_DROPPED
    global SOURCE_DURATION
    FIXTURE_CLIP_FRAMES = clip_frames
    FIXTURE_CLIPS = [(round(a / FPS, 6), round(b / FPS, 6))
                     for a, b in clip_frames]
    FIXTURE_CAPTIONS = captions
    FIXTURE_DROPPED = dropped
    # source must comfortably contain the last clip (+ margin)
    last_out = max(b for _, b in clip_frames) / FPS
    SOURCE_DURATION = max(60.0, round(last_out + 10.0, 3))


def make_scale_fixture(n: int) -> None:
    """n frame-exact clips interleaved with gaps, captions over the record
    timeline, dropped = the gaps. Deterministic (no RNG) for reproducibility."""
    frames: list[tuple[int, int]] = []
    src = 63  # start at 2.1s
    gaps: list[tuple[int, int]] = []
    for i in range(n):
        dur = 75 + (i % 4) * 30            # 2.5s .. 5.5s keeps
        gap = 45 + (i % 3) * 30            # 1.5s .. 3.5s gaps (except after last)
        frames.append((src, src + dur))
        if i < n - 1:
            gaps.append((src + dur, src + dur + gap))
            src += dur + gap
        else:
            src += dur
    # captions every ~4.5s of record time, text cycling the pool
    rec = 0.0
    captions = []
    for i, (s, e) in enumerate(frames):
        dur = (e - s) / FPS
        cs, ce = round(rec + 0.1, 6), round(rec + min(dur - 0.1, 3.5), 6)
        if ce - cs >= 0.4:
            captions.append((cs, ce, CAPTION_POOL[i % len(CAPTION_POOL)]))
        rec = round(rec + dur, 6)
    dropped = [{"reason": ["silence", "retake", "filler"][i % 3],
                "start": round(a / FPS, 6), "end": round(b / FPS, 6)}
               for i, (a, b) in enumerate(gaps)]
    set_fixture(frames, captions, dropped)

TESTS = ("srt", "xml", "edl", "cutlist")
DEFAULT_MODELS = ("gemini-3.5-flash", "gemini-4-argon")


# --------------------------------------------------------------- ground truth
def packed_clips() -> list[dict]:
    """Clips with record positions packed contiguously from 0."""
    out, rec = [], 0.0
    for i, (s, e) in enumerate(FIXTURE_CLIPS):
        out.append({"name": f"cut_{i:04d}", "media": SOURCE,
                    "src_in": s, "src_out": e, "rec_in": round(rec, 6),
                    "duration": round(e - s, 6)})
        rec = round(rec + (e - s), 6)
    return out


def fr(sec: float) -> int:
    return int(round(sec * FPS))


def fmt_tc(sec: float) -> str:
    f = fr(sec)
    ff = f % int(FPS)
    f //= int(FPS)
    ss = f % 60
    f //= 60
    mm = f % 60
    hh = f // 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{int(ff):02d}"


def fmt_srt(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# --------------------------------------------------------------- API plumbing
def load_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("GEMINI_API_KEY not found in .env")


def extract_text(resp: dict) -> str:
    """Interactions REST: steps -> model_output -> content[].text."""
    parts: list[str] = []
    for step in resp.get("steps", []):
        if step.get("type") != "model_output":
            continue
        for c in step.get("content", []):
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
    return "".join(parts)


def strip_fences(t: str) -> str:
    t = t.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[A-Za-z0-9_-]*\n", "", t)
        t = re.sub(r"\n```\s*$", "", t.strip())
    return t.strip()


def _retry_hint(msg: str) -> float | None:
    """'Please retry in 8s' -> 8.0 (Free Tier 429s carry an explicit hint)."""
    m = re.search(r"retry in (\d+)s", msg)
    return float(m.group(1)) if m else None


def call_gemini(key: str, model: str, prompt: str, schema: dict | None = None,
                timeout: int = 360) -> tuple[str, dict]:
    body: dict = {"model": model, "input": prompt, "store": False}
    if schema is not None:
        body["response_format"] = {
            "type": "text", "mime_type": "application/json", "schema": schema,
        }
    last_err = ""
    for attempt in range(6):
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            data=json.dumps(body).encode(),
            headers={"x-goog-api-key": key, "Content-Type": "application/json",
                     "Api-Revision": "2026-05-20"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.load(r)
            text = extract_text(resp)
            if not text.strip():
                last_err = f"empty response (keys={sorted(resp.keys())})"
            else:
                return text, resp.get("usage", {})
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            last_err = f"HTTP {e.code}: {detail}"
            if e.code in (429, 500, 502, 503, 504):
                # honor the server's retry hint; otherwise exponential 3..60s
                wait = max(_retry_hint(detail) or 0.0, min(3 * 2 ** attempt, 60))
                time.sleep(wait)
                continue
            break
        except Exception as e:  # timeout / reset
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(min(3 * 2 ** attempt, 60))
    raise RuntimeError(last_err)


# --------------------------------------------------------------- prompts
def cutlist_json() -> str:
    return json.dumps(
        {"source": SOURCE, "duration": SOURCE_DURATION, "fps": FPS,
         "width": 1920, "height": 1080, "tracks": {"video": packed_clips()},
         "dropped": FIXTURE_DROPPED},
        ensure_ascii=False, indent=1,
    )


def prompt_srt() -> str:
    cues = [{"start": s, "end": e, "text": t} for s, e, t in FIXTURE_CAPTIONS]
    return f"""Write an SRT subtitle file for these caption cues (times are seconds on the record timeline).
Rules: sequential indices starting at 1; timestamp format HH:MM:SS,mmm with milliseconds rounded to nearest ms; one blank line between cues; UTF-8 text verbatim, no re-wrapping.
Worked example: a cue from 0.1s to 2.5s becomes
1
00:00:00,100 --> 00:00:02,500
hello world

Cues:
{json.dumps(cues, ensure_ascii=False, indent=1)}

Output ONLY the SRT content, no commentary, no code fences."""


def prompt_xml() -> str:
    return f"""Convert this cutlist into a Final Cut Pro 7 XML file (xmeml version 4).

Frame math (30 fps, non-drop): frame = round(seconds * 30). All start/end/in/out values are frame numbers and half-open: a clip spans [start, end) on the record timeline and [in, out) in the source. Clips appear on ONE video track in record order (rec_in ascending). Sequence duration = round(total record duration * 30).

Required structure (every element in this exact nesting):
xmeml[version=4] > sequence > (name, duration, rate > timebase=30 + ntsc=FALSE) + media > video > (format > samplecharacteristics > (width, height, rate > timebase=30 + ntsc=FALSE)) + track > clipitem* in record order, each clipitem containing: name, rate > timebase=30 + ntsc=FALSE, start, end, in, out, file > (id unique, name, pathurl, rate > timebase=30 + ntsc=FALSE).

pathurl is a file:// URL of the clip's media (e.g. file:///videos/janani_reel.mp4).

Worked example: a clip with src_in=1.0, src_out=2.0, rec_in=0.0 gives in=30, out=60, start=0, end=30.

Cutlist:
{cutlist_json()}

Output ONLY the XML, starting with <?xml, no commentary, no code fences."""


def prompt_edl() -> str:
    clips = packed_clips()
    rows = "\n".join(
        f"  {c['name']}: src_in={c['src_in']}s src_out={c['src_out']}s rec_in={c['rec_in']}s"
        for c in clips)
    return f"""Write a CMX3600 EDL for the video track of this cutlist (30 fps non-drop, TITLE: hypro_edit, FCM: NON-DROP FRAME).

One event per clip, in record order. Event line layout:
NNN  reelname  V  C  <source_in_tc> <source_out_tc> <record_in_tc> <record_out_tc>
Event number zero-padded to 3, reel name = clip name (no spaces), V = video, C = cut transition. Timecodes are HH:MM:SS:FF where FF = frames at 30 fps: frames = round(seconds * 30).
Record in of clip N = record out of clip N-1; record in of the first clip = 00:00:00:00. Record out = rec_in + (src_out - src_in).
Worked example: src_in=2.1s -> 63 frames -> 00:00:02:03; rec_in=4.333333s -> 130 frames -> 00:00:04:10 (4.333333*30 = 129.99999, rounds to 130 = 4s 10f).

Clips:
{rows}

Output ONLY the EDL text (header lines, blank line, events, trailing blank line), no commentary, no code fences."""


def prompt_cutlist() -> str:
    return f"""Assemble an edit cutlist JSON from these decisions.

Source: {SOURCE}, duration {SOURCE_DURATION}s, fps {FPS}, 1920x1080.
Keep these source ranges (already ordered by start); pack them back-to-back on the record timeline starting at rec_in=0: rec_in of clip 0 = 0.0, rec_in of clip N = sum of durations of clips 0..N-1. Each clip: media={SOURCE}, name=cut_0000-style zero-padded index, role="video".
Also carry the dropped segments through verbatim (same reason/start/end).

Keep ranges: {[(s, e) for s, e in FIXTURE_CLIPS]}
Dropped: {json.dumps(FIXTURE_DROPPED)}

Output JSON with exactly these keys: source (string), duration (number), fps (number), width (number), height (number), tracks (object with key "video" = array of clip objects each with media, name, src_in, src_out, rec_in, role), dropped (array). Numbers with up to 6 decimals."""


CUTLIST_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "duration": {"type": "number"},
        "fps": {"type": "number"},
        "width": {"type": "integer"},
        "height": {"type": "integer"},
        "tracks": {
            "type": "object",
            "properties": {
                "video": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "media": {"type": "string"},
                            "name": {"type": "string"},
                            "src_in": {"type": "number"},
                            "src_out": {"type": "number"},
                            "rec_in": {"type": "number"},
                            "role": {"type": "string"},
                        },
                        "required": ["media", "name", "src_in", "src_out", "rec_in", "role"],
                    },
                }
            },
            "required": ["video"],
        },
        "dropped": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                },
                "required": ["reason", "start", "end"],
            },
        },
    },
    "required": ["source", "duration", "fps", "width", "height", "tracks", "dropped"],
}


# --------------------------------------------------------------- validators
def validate_srt(text: str) -> list[str]:
    import pysubs2

    cues = FIXTURE_CAPTIONS
    try:
        subs = pysubs2.SSAFile.from_string(text, format_="srt")
    except Exception as e:
        return [f"SRT parse error: {e}"]
    errs: list[str] = []
    if len(subs) != len(cues):
        errs.append(f"cue count {len(subs)} != {len(cues)}")
    for i, (ev, (s, e, t)) in enumerate(zip(subs, cues), 1):
        if abs(ev.start - round(s * 1000)) > 1:
            errs.append(f"cue {i} start {ev.start}ms != {round(s * 1000)}ms")
        if abs(ev.end - round(e * 1000)) > 1:
            errs.append(f"cue {i} end {ev.end}ms != {round(e * 1000)}ms")
        got = re.sub(r"\s+", " ", ev.text.replace("\\N", " ")).strip()
        want = re.sub(r"\s+", " ", t).strip()
        if got != want:
            errs.append(f"cue {i} text {got!r} != {want!r}")
    return errs


def validate_xml(text: str) -> list[str]:
    clips = packed_clips()
    errs: list[str] = []
    try:
        root = ET.fromstring(text)
    except Exception as e:
        return [f"XML parse error: {e}"]
    if root.tag != "xmeml":
        errs.append(f"root <{root.tag}> != xmeml")
    # OTIO nests it at xmeml/project/children/sequence; a bare
    # xmeml/sequence also appears in the wild — accept the first anywhere.
    seq = root.find("project/children/sequence")
    if seq is None:
        seq = root.find(".//sequence")
    if seq is None:
        return errs + ["no <sequence>"]
    tb = seq.findtext("rate/timebase")
    if tb is None or int(float(tb)) != int(FPS):
        errs.append(f"sequence timebase {tb!r} != {int(FPS)}")
    ntsc = seq.findtext("rate/ntsc")
    if ntsc is not None and ntsc.strip().upper() != "FALSE":
        errs.append(f"sequence ntsc {ntsc!r} != FALSE (would mean {FPS / 1.001} fps)")
    exp_dur = fr(clips[-1]["rec_in"] + clips[-1]["duration"])
    dur = seq.findtext("duration")
    if dur is None or int(dur) != exp_dur:
        errs.append(f"sequence duration {dur!r} != {exp_dur}")
    tracks = seq.findall("media/video/track")
    if not tracks:
        return errs + ["no media/video/track"]
    # xmeml dedups media: only the first <file id="..."> carries children,
    # later clipitems reference it with <file id="..."/> — resolve by id.
    file_urls: dict[str, str] = {}
    for fe in seq.iter("file"):
        fid = fe.get("id")
        url = fe.findtext("pathurl")
        if fid and url:
            file_urls[fid] = url
    items = tracks[0].findall("clipitem")
    if len(items) != len(clips):
        errs.append(f"clipitem count {len(items)} != {len(clips)}")
    for i, (it, c) in enumerate(zip(items, clips)):
        def ival(tag: str) -> int | None:
            v = it.findtext(tag)
            return int(v) if v is not None and re.fullmatch(r"-?\d+", v.strip()) else None

        exp = {"start": fr(c["rec_in"]),
               "end": fr(c["rec_in"] + c["duration"]),
               "in": fr(c["src_in"]), "out": fr(c["src_out"])}
        for tag, want in exp.items():
            got = ival(tag)
            if got != want:
                errs.append(f"clip {i} ({c['name']}) {tag} {got} != {want}")
        fe = it.find("file")
        pathurl = (fe.findtext("pathurl") if fe is not None else None) or \
                  file_urls.get(fe.get("id") if fe is not None else "", "") or ""
        if Path(SOURCE).name not in pathurl:
            errs.append(f"clip {i} pathurl {pathurl!r} missing {Path(SOURCE).name}")
    return errs


EDL_EVENT = re.compile(
    r"^(\d{3})\s+(\S+)\s+V\s+C\s+"
    r"(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})\s+"
    r"(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})\s*$")


def validate_edl(text: str) -> list[str]:
    clips = packed_clips()
    errs: list[str] = []
    if not re.search(r"^TITLE:\s*\S+", text, re.M):
        errs.append("missing TITLE line")
    if "FCM: NON-DROP FRAME" not in text:
        errs.append("missing FCM: NON-DROP FRAME")
    events = []
    for line in text.splitlines():
        m = EDL_EVENT.match(line.strip())
        if m:
            events.append(m.groups())
    if len(events) != len(clips):
        errs.append(f"event count {len(events)} != {len(clips)}")
        return errs
    rec = 0.0
    for i, (ev, c) in enumerate(zip(events, clips), 1):
        num, reel, sin, sout, rin, rout = ev
        if int(num) != i:
            errs.append(f"event {i} number {num} != {i:03d}")
        exp = (fmt_tc(c["src_in"]), fmt_tc(c["src_out"]),
               fmt_tc(rec), fmt_tc(rec + c["duration"]))
        got = (sin, sout, rin, rout)
        if got != exp:
            errs.append(f"event {i} timecodes {got} != {exp}")
        rec += c["duration"]
    return errs


def validate_cutlist(text: str) -> list[str]:
    errs: list[str] = []
    try:
        obj = json.loads(text)
    except Exception as e:
        return [f"JSON parse error: {e}"]
    clips = packed_clips()
    vid = (obj.get("tracks") or {}).get("video") or []
    if len(vid) != len(clips):
        errs.append(f"video clip count {len(vid)} != {len(clips)}")
        return errs
    rec = 0.0
    for i, (g, w) in enumerate(zip(vid, clips)):
        for tag in ("src_in", "src_out", "rec_in"):
            gv, wv = g.get(tag), w[tag]
            if not isinstance(gv, (int, float)) or abs(gv - wv) > 1e-3:
                errs.append(f"clip {i} {tag} {gv!r} != {wv}")
        if g.get("media") != SOURCE:
            errs.append(f"clip {i} media {g.get('media')!r} != {SOURCE!r}")
        rec += w["src_out"] - w["src_in"]
    for tag, want in (("duration", SOURCE_DURATION), ("fps", FPS),
                      ("width", 1920), ("height", 1080)):
        gv = obj.get(tag)
        if not isinstance(gv, (int, float)) or abs(gv - want) > 1e-3:
            errs.append(f"{tag} {gv!r} != {want}")
    dropped = obj.get("dropped") or []
    if len(dropped) != len(FIXTURE_DROPPED):
        errs.append(f"dropped count {len(dropped)} != {len(FIXTURE_DROPPED)}")
    else:
        for i, (g, w) in enumerate(zip(dropped, FIXTURE_DROPPED)):
            if g.get("reason") != w["reason"] or abs(g.get("start", -1) - w["start"]) > 1e-3 \
                    or abs(g.get("end", -1) - w["end"]) > 1e-3:
                errs.append(f"dropped {i} {g} != {w}")
    return errs


# --------------------------------------------------------------- orchestration
def run_test(key: str, model: str, test: str) -> tuple[list[str], dict, str]:
    """Returns (errors, usage, raw_output). errors == [] means PASS."""
    if test == "srt":
        text, usage = call_gemini(key, model, prompt_srt())
        return validate_srt(text), usage, text
    if test == "xml":
        text, usage = call_gemini(key, model, prompt_xml())
        return validate_xml(text), usage, text
    if test == "edl":
        text, usage = call_gemini(key, model, prompt_edl())
        return validate_edl(text), usage, text
    if test == "cutlist":
        text, usage = call_gemini(key, model, prompt_cutlist(), schema=CUTLIST_SCHEMA)
        return validate_cutlist(strip_fences(text)), usage, text
    raise ValueError(test)


def check_fixture_math() -> None:
    """Sanity: our own exporter must agree with the independent ground truth."""
    from hypro_core.cutlist import CaptionCue, Clip, Cutlist
    from hypro_core.export.nle import export_nle

    cl = Cutlist(
        source=SOURCE, duration=SOURCE_DURATION, fps=FPS, width=1920, height=1080,
        tracks={"video": [Clip(media=c["media"], src_in=c["src_in"], src_out=c["src_out"],
                               rec_in=c["rec_in"], name=c["name"], role="video")
                          for c in packed_clips()]},
        captions=[CaptionCue(start=s, end=e, text=t) for s, e, t in FIXTURE_CAPTIONS],
        dropped=list(FIXTURE_DROPPED),
    )
    out = ROOT / "tmp" / "gemini_probe_reference"
    written = export_nle(cl, out, name="edit")
    errs: list[str] = []
    xml_text = Path(written["xml"]).read_text(encoding="utf-8")
    for e in validate_xml(xml_text):
        errs.append(f"reference XML: {e}")
    srt_text = Path(written["srt"]).read_text(encoding="utf-8")
    for e in validate_srt(srt_text):
        errs.append(f"reference SRT: {e}")
    if errs:
        print("FIXTURE MATH DISAGREES WITH OUR EXPORTER:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        raise SystemExit(2)
    print(f"fixture sanity: our exporter passes all validators "
          f"({len(FIXTURE_CLIPS)} clips, {len(FIXTURE_CAPTIONS)} cues) -> {out}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--tests", default=",".join(TESTS))
    ap.add_argument("--scale", type=int, default=0,
                    help="regenerate the fixture with N clips (0 = the 4-clip base)")
    ap.add_argument("--raw-dir", default=str(ROOT / "tmp" / "gemini_probe_raw"))
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    tests = [t.strip() for t in args.tests.split(",") if t.strip()]
    for t in tests:
        if t not in TESTS:
            ap.error(f"unknown test {t!r} (choose from {TESTS})")
    if args.scale:
        make_scale_fixture(args.scale)
        print(f"scale fixture: {args.scale} clips, "
              f"{len(FIXTURE_CAPTIONS)} cues, source {SOURCE_DURATION}s")

    key = load_key()
    check_fixture_math()
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for model in models:
        for test in tests:
            t0 = time.time()
            try:
                errs, usage, raw = run_test(key, model, test)
                exc = None
            except RuntimeError as e:
                errs, usage, raw, exc = [f"CALL FAILED: {e}"], {}, "", str(e)
            dt = time.time() - t0
            (raw_dir / f"{model}__{test}.txt").write_text(raw, encoding="utf-8")
            status = "PASS" if not errs else "FAIL"
            print(f"[{status}] {model:22s} {test:8s} {dt:5.1f}s  "
                  f"out={usage.get('total_output_tokens', '?')} tok "
                  f"(thought={usage.get('total_thought_tokens', 0)})")
            for e in errs[:12]:
                print(f"         - {e}")
            if len(errs) > 12:
                print(f"         ... +{len(errs) - 12} more")
            results.append({"model": model, "test": test, "status": status,
                            "errors": errs, "seconds": round(dt, 1),
                            "usage": usage, "exception": exc,
                            "raw_file": str(raw_dir / f"{model}__{test}.txt")})

    tag = f"scale{args.scale}" if args.scale else "base"
    slug = "-".join(m.replace("gemini-", "") for m in models)
    out_json = ROOT / "tmp" / f"gemini_probe_results_{tag}_{slug}.json"
    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print(f"\n=== summary ({tag}) ===")
    width = max(len(m) for m in models)
    header = f"{'model':{width}s}  " + "  ".join(f"{t:8s}" for t in tests)
    print(header)
    for model in models:
        cells = []
        for t in tests:
            r = next((x for x in results if x["model"] == model and x["test"] == t), None)
            cells.append(f"{'—':8s}" if r is None else f"{r['status']:8s}")
        print(f"{model:{width}s}  " + "  ".join(cells))
    print(f"\nresults -> {out_json}")

    failed = sum(1 for r in results if r["status"] != "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
