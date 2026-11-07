"""NLE export layer — one folder, five files (research 08 §7).

    <name>.xml       FCP7 XML (xmeml)  → Premiere Pro + DaVinci Resolve (v17+)
    <name>.otio      open timeline     → Resolve v17+, future-proofing
    <name>.srt       captions          → sidecar (FCP7 XML has no subtitle track)
    <name>.jsx       After Effects     → ExtendScript rebuilds the comp (Route B)
    cutlist.json     ground truth      → diffable, fallback source
    tracks/*.edl     CMX3600           → belt-and-braces, one per track

Track layout (verified against Premiere's own export in research 08):
    first <track> in <video> = V1  → A-roll (jump cuts = separate clipitems)
    V2 = b-roll · A1 = music · A2 = voice (put A-roll audio on A2, identical
    in/out — OTIO emits no <link>, so A/V arrive unlinked)

Captions never go inside FCP7 XML — .srt sidecar is the universal answer, and
the sequence must start at 00:00:00:00 or the SRT shifts.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ..cutlist import CaptionCue, Clip, Cutlist

TRACK_ORDER = ["video", "broll", "music", "voice"]  # → V1, V2, A1, A2


def _fmt_tc(seconds: float, fps: float) -> str:
    """NLE-style HH:MM:SS:FF timecode (drop-frame-free)."""
    rate = max(1, int(round(fps)))          # 29.97 → 30 (NDF convention);
    total = int(round(seconds * fps))       # int math or hh/mm/ss go float
    ff = total % rate
    total //= rate
    ss = total % 60
    total //= 60
    mm = total % 60
    hh = total // 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{int(ff):02d}"


# --------------------------------------------------------------------- SRT
def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[CaptionCue], path: Path) -> None:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n")
    path.write_text("\n".join(out), encoding="utf-8")


# --------------------------------------------------------------------- EDL
def _write_audio_edl(clips: list, path: Path, fps: float, title: str) -> None:
    """Minimal CMX3600 for an audio track.

    The otio cmx_3600 adapter only accepts timelines with exactly one VIDEO
    track, so audio roles (voice/music) are emitted by hand: one event per
    clip, channel A, cut transitions, source in/out → record in/out.
    """
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    for i, c in enumerate(sorted(clips, key=lambda x: x.rec_in), 1):
        lines.append(
            f"{i:03d}  AX     A     C     "
            f"{_fmt_tc(c.src_in, fps)} {_fmt_tc(c.src_out, fps)} "
            f"{_fmt_tc(c.rec_in, fps)} {_fmt_tc(c.rec_in + c.duration, fps)}"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------- OTIO
def _voice_clips(cl: Cutlist) -> list[Clip]:
    """A-roll audio for the A2 (VOICE) track — research 08 §"A/V linking".

    OTIO's FCP7 writer emits no <link>/<sourcetrack>, and a cutlist's video
    clips carry no audio, so without an explicit audio track every NLE import
    arrives SILENT (verified: empty <audio><track/> in xmeml, hasAudio="0"
    in FCPXML). The source of a speech pipeline is a speech recording, so
    mirror the video cuts onto VOICE with identical in/out — the editor gets
    sync audio and just links V1↔A2. An explicit voice track (populated by
    future audio separation) wins, and meta.has_audio=False (silent source)
    suppresses the mirror.
    """
    explicit = cl.tracks.get("voice") or []
    if explicit:
        return explicit
    if not cl.meta.get("has_audio", True):
        return []
    return [replace(c, role="voice") for c in cl.tracks.get("video", [])]


def _build_otio(cl: Cutlist, *, compact: bool = False,
                mirror_audio: bool = True):
    """Build an OTIO timeline from a cutlist.

    compact=False (default): every role in TRACK_ORDER becomes a track, even
    empty ones — structure preserved for FCP7 XML round-trips.
    compact=True: skip empty tracks — required by the cmx_3600 EDL adapter,
    which rejects timelines with more than one video track.
    mirror_audio=False: don't synthesize the VOICE track (per-track EDLs
    stay single-role — a video EDL must not grow an audio track).
    """
    import opentimelineio as otio

    rate = cl.fps or 30.0

    def rt(sec: float):
        return otio.opentime.RationalTime(int(round(sec * rate)), rate)

    def trange(s: float, dur: float):
        return otio.opentime.TimeRange(rt(s), rt(dur))

    timeline = otio.schema.Timeline(Path(cl.source).stem)

    for role in TRACK_ORDER:
        if role == "voice" and mirror_audio:
            source = _voice_clips(cl)
        else:
            source = cl.tracks.get(role, [])
        clips = sorted(source, key=lambda x: x.rec_in)
        if compact and not clips:
            continue
        kind = (otio.schema.TrackKind.Video if role in ("video", "broll")
                else otio.schema.TrackKind.Audio)
        track = otio.schema.Track(name=role.upper(), kind=kind)
        pos = 0.0
        for c in clips:
            ref = otio.schema.ExternalReference(
                target_url=Path(c.media).absolute().as_uri(),
                available_range=trange(0.0, max(c.duration + c.src_in, c.src_out)),
            )
            clip = otio.schema.Clip(
                name=c.name or Path(c.media).stem,
                media_reference=ref,
                source_range=trange(c.src_in, c.duration),
            )
            if role == "voice":
                # fcp_xml dedupes clipitems by serialized equality: a voice
                # clip identical to its V1 twin would collapse to an empty
                # <clipitem id="clipitem-1"/> backref — an OTIO reader idiom
                # Premiere doesn't write (real exports always have full
                # clipitems; only <file> is abbreviated) and may not resolve,
                # which would re-create the silent import. Metadata breaks
                # the tie and stays out of the XML (namespace isn't fcp_xml).
                clip.metadata["hypro"] = {"track": role}
            # gaps before the clip become Gaps so record timing is exact
            if c.rec_in > pos + 1e-6:
                track.append(otio.schema.Gap(source_range=trange(0.0, c.rec_in - pos)))
            track.append(clip)
            pos = c.rec_in + c.duration
        timeline.tracks.append(track)

    # markers for dropped (cut) ranges — visible as review markers in NLEs
    # (OTIO puts markers on tracks/stacks, not on Timeline directly)
    if timeline.tracks and cl.dropped:
        anchor = timeline.tracks[0]
        for d in cl.dropped:
            if "start" in d and "end" in d:
                anchor.markers.append(
                    otio.schema.Marker(
                        name=f"cut:{d.get('reason', 'dropped')}",
                        marked_range=trange(float(d["start"]),
                                            max(float(d["end"]) - float(d["start"]), 0.01)),
                    )
                )
    return timeline


# --------------------------------------------------------------------- JSX (AE)
_AE_JSX = """// Auto-generated by hypro-core — rebuilds the edit in After Effects.
// AE: File ▸ Scripts ▸ Run Script File ▸ <name>.jsx
// Route B from research 08: works without Premiere and without paid plug-ins.
var CUTLIST = __CUTLIST__;

app.beginUndoGroup("hypro-core import");
var comp = app.project.items.addComp(
    CUTLIST.meta.name || "hypro_export",
    CUTLIST.width || 1920, CUTLIST.height || 1080,
    1, CUTLIST.rec_duration || 10, CUTLIST.fps || 30);

var vids = CUTLIST.tracks.video || [];
for (var i = 0; i < vids.length; i++) {
    var c = vids[i];
    var fo = new File(c.media);
    if (!fo.exists) { continue; }
    var item = app.project.importFile(new ImportOptions(fo));
    var layer = comp.layers.add(item);
    layer.name = c.name || ("cut_" + i);
    layer.startTime = c.rec_in;
    layer.inPoint = c.rec_in;
    layer.outPoint = c.rec_in + (c.src_out - c.src_in);
    // source trim: shift the layer so the right source range shows
    layer.startTime = c.rec_in - c.src_in;
    layer.inPoint = c.rec_in;
}
app.endUndoGroup();
alert("hypro-core: " + vids.length + " cuts imported into a new comp.");
"""


# --------------------------------------------------------------------- main
def export_nle(cl: Cutlist, out_dir: str | Path, name: str = "edit") -> dict:
    """Write the full export folder. Returns {kind: path} of files written."""
    import opentimelineio as otio

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    # 1. cutlist.json — ground truth (always first; everything else derives)
    cl_path = out / "cutlist.json"
    cl.save(cl_path)
    written["cutlist"] = str(cl_path)

    # 2. captions sidecar
    if cl.captions:
        srt_path = out / f"{name}.srt"
        write_srt(cl.captions, srt_path)
        written["srt"] = str(srt_path)

    # 3. OTIO timeline → FCP7 XML + native .otio
    tl = _build_otio(cl)
    xml_path = out / f"{name}.xml"
    otio.adapters.write_to_file(tl, str(xml_path), adapter_name="fcp_xml")
    written["xml"] = str(xml_path)
    otio_path = out / f"{name}.otio"
    otio.adapters.write_to_file(tl, str(otio_path), adapter_name="otio_json")
    written["otio"] = str(otio_path)

    # 3b. FCPXML → Final Cut Pro 10.4+ (FCP imports FCPXML only — research 08
    # §1.4 — so .xml alone leaves FCP users out). The adapter is a separate
    # package; if it's absent the export still succeeds and HOWTO says so.
    fcpx_ok = False
    try:
        fcpx_path = out / f"{name}.fcpxml"
        otio.adapters.write_to_file(tl, str(fcpx_path), adapter_name="fcpx_xml")
        # The adapter also drops a root-level <asset-clip> per source file
        # beside <project> (its reader tolerates that; FCP may not — it would
        # be a second, untrimmed copy of the whole source). Canonical FCPXML
        # is <resources> + <project> only (adapter's own fcpx_project sample),
        # so strip anything else at the root before shipping.
        import xml.etree.ElementTree as ET
        tree = ET.parse(fcpx_path)
        root = tree.getroot()
        for child in list(root):
            if child.tag not in ("resources", "library", "event", "project"):
                root.remove(child)
        tree.write(fcpx_path, encoding="UTF-8", xml_declaration=True)
        written["fcpxml"] = str(fcpx_path)
        fcpx_ok = True
    except Exception:  # adapter not installed — degrade, never fail the export
        pass

    # 4. EDL fallback — one CMX3600 per track (style="premiere")
    edl_dir = out / "tracks"
    edl_dir.mkdir(exist_ok=True)
    voice_clips = _voice_clips(cl)
    for role in TRACK_ORDER:
        track_clips = voice_clips if role == "voice" else cl.tracks.get(role, [])
        if not track_clips:
            continue
        one = Cutlist(source=cl.source, duration=cl.duration, fps=cl.fps,
                      width=cl.width, height=cl.height,
                      tracks={role: track_clips}, meta=dict(cl.meta))
        p = edl_dir / f"{role}.edl"
        if role in ("video", "broll"):
            # mirror_audio=False: per-track EDLs stay single-role
            otio.adapters.write_to_file(_build_otio(one, compact=True,
                                                    mirror_audio=False),
                                        str(p), adapter_name="cmx_3600")
        else:
            _write_audio_edl(track_clips, p, cl.fps or 30.0, title=role)
        written[f"edl:{role}"] = str(p)

    # 5. After Effects Route B
    jsx_payload = cl.to_dict()
    jsx_payload["rec_duration"] = cl.rec_duration()
    jsx_payload["meta"] = dict(cl.meta, name=name)
    jsx = out / f"{name}.jsx"
    jsx.write_text(_AE_JSX.replace("__CUTLIST__", json.dumps(jsx_payload, ensure_ascii=False)),
                   encoding="utf-8")
    written["jsx"] = str(jsx)

    # README with per-editor import instructions
    (out / "HOWTO.txt").write_text(
        f"""hypro-core export — {name}
{'=' * 50}
Premiere Pro   : File ▸ Import ▸ {name}.xml   (V1=A-roll, V2=b-roll, A1=music, A2=voice)
                 File ▸ Import ▸ {name}.srt    (captions → caption track)
DaVinci Resolve: File ▸ Import ▸ Timeline ▸ {name}.xml   (or {name}.otio, v17+)
                 File ▸ Import ▸ Subtitle ▸ {name}.srt
After Effects  : File ▸ Scripts ▸ Run Script File ▸ {name}.jsx
                 (or: import {name}.xml in Premiere ▸ save ▸ File ▸ Import ▸ Premiere Project)
Final Cut Pro  : File ▸ Import ▸ XML… ▸ {name}.fcpxml   (FCP 10.4+; needs
                 otio-fcpx-xml-adapter — {('written ✓' if fcpx_ok else 'NOT installed, pip install otio-fcpx-xml-adapter then re-run `hypro export`')})
                 captions: {name}.srt ▸ File ▸ Import ▸ Captions… (or SRT drag-in)
Belt & braces   : tracks/*.edl (CMX3600, one per track) + cutlist.json

Known gaps (research 08): track NAMES aren't carried by FCP7 XML (V1/A1 only);
A2 carries the a-roll audio in sync, but V1↔A2 arrive unlinked — select both
and Link (Ctrl/Cmd+L) — or use AAF on Windows.
Sequence starts at 00:00:00:00 so the SRT lands correctly.
""",
        encoding="utf-8",
    )
    written["howto"] = str(out / "HOWTO.txt")
    return written
