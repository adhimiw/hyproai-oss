"""Render — keep-ranges → edited video (research/05 §4 render primitive).

The primitive from research/05 §1.3/§4: one `filter_complex` re-encode —
per keep range `trim`/`atrim` with PTS reset → `concat=n=N:v=1:a=1` →
optional `ass=` burn → libx264 (crf) + AAC. Frame-accurate and A/V locked;
output duration == sum of the keep ranges.

Two details that are load-bearing:

* **Captions burn AFTER concat.** ASS cues are on the RECORD timeline
  (what the viewer sees); burning per segment would replay every cue inside
  every segment at the wrong time. So the order is trim → concat → ass.

* **Joins use a 15 ms per-segment fade, not acrossfade.** research/05 §1.6
  asks for a 10–30 ms equal-power micro-fade to kill clicks at off-zero
  cuts. A real crossfade (`acrossfade`) shortens audio by its duration at
  every join — at N=200 that is seconds of A/V drift — so instead each
  segment gets `afade` in/out (duration-preserving). Video stays a hard cut
  (research/05 §4: long crossfades on dialogue read as mistakes).

Large N: the single graph is verified working past 200 concat inputs, but a
conservative cap keeps graphs small; above it we fall back to segment-wise
re-encodes + a concat-demuxer list + a final ass/encode pass (or stream
copy when no captions are requested), which is linear in N and never
builds a giant graph.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from .media import probe_media

__all__ = ["render_edited"]

_FADE_S = 0.015              # 15 ms micro-fade at every join (research/05 §4)
_FADE_MIN_SEGMENT = 0.08     # below this the fade would swallow the segment
_MAX_SINGLE_RANGES = 120     # above this use the segment-wise fallback
_MAX_GRAPH_CHARS = 60_000    # belt-and-braces cap on filter_complex length
# ffmpeg filter option values only reliably quote with '...', and a literal
# ' cannot appear inside — any other char in the ASS path gets a temp copy.
_SAFE_ASS_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-~"
)


def _run_ffmpeg(cmd: list[str]) -> str:
    """Run ffmpeg, return stderr; RuntimeError with stderr tail on failure."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:  # pragma: no cover - environment problem
        raise RuntimeError("ffmpeg not found — install ffmpeg") from e
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-600:]
        raise RuntimeError(
            f"ffmpeg failed (exit {proc.returncode}) running "
            f"{' '.join(cmd[:6])}…:\n{tail}"
        )
    return proc.stderr or ""


def _normalize_keep(keep: list, duration: float, fps: float) -> list[tuple[float, float]]:
    """Sort, clamp to [0, duration], drop unusable ranges, merge overlaps.

    * out-of-bounds ends are clamped to the source duration;
    * zero-length (and fully out-of-bounds) ranges are skipped;
    * ranges shorter than one video frame are skipped: they would contribute
      0 video frames but non-zero audio and silently desync concat (the
      analysis stages never emit such ranges — their floor is 0.3 s);
    * overlapping ranges are merged so the output duration equals the
      *union* of what was asked for, not a double-counted sum.
    """
    if not keep:
        raise ValueError("keep is empty — nothing to render")
    if duration <= 0:
        raise RuntimeError("cannot determine source duration")
    min_len = max(1e-6, 1.0 / fps if fps and fps > 0 else 1.0 / 30)
    ranges: list[tuple[float, float]] = []
    for item in keep:
        try:
            s, e = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError(
                f"malformed keep range {item!r} — expected (start, end) seconds"
            ) from exc
        s = min(max(s, 0.0), duration)
        e = min(max(e, 0.0), duration)
        # 1e-9 slack: an exact one-frame range (e - s == 1/fps) must survive
        # the float subtraction.
        if e - s < min_len - 1e-9:
            continue
        ranges.append((s, e))
    if not ranges:
        raise ValueError(
            "no usable keep ranges after clamping to source duration "
            f"{duration:.3f}s — check the keep list"
        )
    ranges.sort()
    merged = [ranges[0]]
    for s, e in ranges[1:]:
        ps, pe = merged[-1]
        if s <= pe + 1e-6:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def _build_single_graph(ranges: list[tuple[float, float]], has_audio: bool,
                        ass_path: str | None) -> tuple[str, str]:
    """Build the one-pass filter_complex; returns (graph, output_v_label).

    Every trim reads [0:v]/[0:a] — ffmpeg auto-inserts a split fan-out — so
    the graph needs only one input no matter how many ranges.
    """
    chains: list[str] = []
    labels: list[str] = []
    for idx, (s, e) in enumerate(ranges):
        chains.append(f"[0:v]trim=start={s:.6f}:end={e:.6f},setpts=PTS-STARTPTS[v{idx}]")
        if has_audio:
            dur = e - s
            a = f"[0:a]atrim=start={s:.6f}:end={e:.6f},asetpts=PTS-STARTPTS"
            if dur >= _FADE_MIN_SEGMENT:
                a += (f",afade=t=in:st=0:d={_FADE_S:g}"
                      f",afade=t=out:st={dur - _FADE_S:.6f}:d={_FADE_S:g}")
            chains.append(f"{a}[a{idx}]")
            labels.append(f"[v{idx}][a{idx}]")
        else:
            labels.append(f"[v{idx}]")
    n = len(ranges)
    if has_audio:
        chains.append(f"{''.join(labels)}concat=n={n}:v=1:a=1[vc][ac]")
    else:
        chains.append(f"{''.join(labels)}concat=n={n}:v=1:a=0[vc]")
    vlabel = "vc"
    if ass_path:
        # Record-timeline burn, after concat (module docstring).
        chains.append(f"[vc]ass='{ass_path}'[vo]")
        vlabel = "vo"
    return ";".join(chains), vlabel


def _render_single(src: str, graph: str, vlabel: str, dst: str,
                   has_audio: bool, crf: int, preset: str) -> None:
    """One filter_complex pass: trim → concat → (ass) → libx264 + AAC."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
           "-filter_complex", graph, "-map", f"[{vlabel}]"]
    if has_audio:
        cmd += ["-map", "[ac]"]
    cmd += ["-c:v", "libx264", "-crf", str(crf), "-preset", preset,
            "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += _movflags(dst) + [dst]
    _run_ffmpeg(cmd)


def _render_fallback(src: str, ranges: list[tuple[float, float]], dst: str,
                     has_audio: bool, ass_path: str | None,
                     crf: int, preset: str) -> None:
    """Segment-wise path for very large N (contract: up to ~200+ ranges).

    Each range is re-encoded to its own intermediate clip (accurate input
    seek + exact `-t`; intermediate re-encode is required — stream copy would
    cut on keyframes), the clips are joined by the concat *demuxer*, then a
    final pass either burns the ASS file (re-encode) or stream-copies the
    already-target-quality video (a single generation of loss total).
    """
    out_dir = Path(dst).resolve().parent
    # Concat-demuxer list entries are single-quoted: keep the temp dir in the
    # output folder (big renders don't fit in /tmp) unless it can't be
    # quoted safely.
    tmp_parent = None if "'" in str(out_dir) else str(out_dir)
    with tempfile.TemporaryDirectory(prefix="hypro_render_", dir=tmp_parent) as td:
        tdir = Path(td)
        seg_paths: list[Path] = []
        for idx, (s, e) in enumerate(ranges):
            seg = tdir / f"seg_{idx:04d}.mp4"
            # Floor to microseconds: rounding -ss UP would skip a frame that
            # sits exactly on the boundary (1/30 s is not µs-representable).
            seek = math.floor(s * 1e6) / 1e6
            end = math.floor(e * 1e6) / 1e6
            dur = max(end - seek, 1e-6)
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                   "-ss", f"{seek:.6f}", "-i", src, "-t", f"{dur:.6f}",
                   "-map", "0:v:0"]
            if has_audio:
                cmd += ["-map", "0:a:0"]
            cmd += ["-c:v", "libx264", "-crf", str(crf), "-preset", preset,
                    "-pix_fmt", "yuv420p"]
            if has_audio:
                cmd += ["-c:a", "aac", "-b:a", "192k"]
                if dur >= _FADE_MIN_SEGMENT:
                    cmd += ["-af",
                            f"afade=t=in:st=0:d={_FADE_S:g},"
                            f"afade=t=out:st={dur - _FADE_S:.6f}:d={_FADE_S:g}"]
            cmd += [str(seg)]
            _run_ffmpeg(cmd)
            seg_paths.append(seg)

        list_path = tdir / "list.txt"
        list_path.write_text(
            "".join(f"file '{p}'\n" for p in seg_paths), encoding="utf-8"
        )
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-f", "concat", "-safe", "0", "-i", str(list_path)]
        if ass_path:
            cmd += ["-vf", f"ass='{ass_path}'",
                    "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
                    "-pix_fmt", "yuv420p"]
            if has_audio:
                cmd += ["-c:a", "aac", "-b:a", "192k"]
        else:
            # Segments already carry the target quality → no second video
            # generation: plain stream copy.
            cmd += ["-c", "copy"]
        cmd += _movflags(dst) + [dst]
        _run_ffmpeg(cmd)


def _movflags(dst: str) -> list[str]:
    """faststart only for MP4-family containers (other muxers reject it)."""
    return (["-movflags", "+faststart"]
            if Path(dst).suffix.lower() in {".mp4", ".m4v", ".mov"} else [])


def render_edited(video: str, keep: list[tuple[float, float]], out: str, *,
                  ass: str | None = None, crf: int = 18,
                  preset: str = "medium") -> str:
    """Render the edited video from source-timeline keep ranges.

    `keep` is a list of (start, end) seconds on the SOURCE timeline (what
    Cutlist.keep_ranges() returns); they are packed back-to-back on the
    output timeline, so output duration == sum of the ranges (± frame
    rounding). Ranges are sorted, clamped to the source duration,
    overlaps merged, empty/sub-frame ranges dropped (see _normalize_keep).

    `ass` is an optional subtitle file burned on the RECORD timeline — the
    burn happens after all segments are concatenated, which is what makes
    record-time cue timestamps correct (research/05 §4 render strategy).

    Single `filter_complex` pass for ≤120 ranges (verified working past 200
    concat inputs); larger edits fall back to segment-wise re-encode +
    concat demuxer. No-audio sources render as a video-only concat.
    Returns the output path; raises ValueError/RuntimeError/FileNotFoundError
    with a readable message on bad input or ffmpeg failure.
    """
    src, dst = str(video), str(out)
    info = probe_media(src)
    if not info.has_video:
        raise RuntimeError(f"no video stream in {src} — render_edited edits video")
    if ass is not None and not Path(ass).exists():
        raise FileNotFoundError(str(ass))
    ranges = _normalize_keep(keep, info.duration, info.fps)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)

    # A path that filtergraph quoting can't represent (e.g. contains a quote
    # or a space) is copied to a safe temp file for the duration of the run.
    tmp_dir: str | None = None
    ass_path: str | None = None
    try:
        if ass is not None:
            resolved = str(Path(ass).resolve())
            if set(resolved) <= _SAFE_ASS_CHARS:
                ass_path = resolved
            else:
                tmp_dir = tempfile.mkdtemp(prefix="hypro_subs_")
                ass_path = str(Path(tmp_dir) / "subs.ass")
                shutil.copyfile(ass, ass_path)

        graph = vlabel = None
        if len(ranges) <= _MAX_SINGLE_RANGES:
            graph, vlabel = _build_single_graph(ranges, info.has_audio, ass_path)
            if len(graph) > _MAX_GRAPH_CHARS:
                graph = vlabel = None
        if graph is not None and vlabel is not None:
            _render_single(src, graph, vlabel, dst, info.has_audio, crf, preset)
        else:
            _render_fallback(src, ranges, dst, info.has_audio, ass_path, crf, preset)
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if not Path(dst).exists():
        raise RuntimeError(f"ffmpeg reported success but {dst} was not created")
    return dst
