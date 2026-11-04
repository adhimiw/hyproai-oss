"""Cutlist — the canonical edit-decision format shared by every stage.

    analyze (silence/retake/filler)  → produces keep-ranges → Cutlist
    render   (ffmpeg)                → consumes Cutlist → edited MP4
    export   (NLE)                   → consumes Cutlist → XML/EDL/OTIO/SRT/JSX

A cutlist is deliberately boring JSON: it is the regression fixture, the
"edit as code" artifact you can diff in git, and the fallback source if every
NLE import fails (research 08 §6).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CUTLIST_VERSION = 1

# The five layers HyproAI exports (research 08 §4.1 track mapping)
ROLES = ("video", "broll", "music", "voice", "captions")


@dataclass
class Clip:
    """One clip on a track. Times in seconds (floats)."""

    media: str            # absolute or cutlist-relative path to source file
    src_in: float         # in-point in the SOURCE media
    src_out: float        # out-point in the source media
    rec_in: float         # where it lands on the RECORD timeline
    name: str = ""
    role: str = "video"

    @property
    def duration(self) -> float:
        return self.src_out - self.src_in


@dataclass
class CaptionCue:
    start: float          # record-timeline seconds
    end: float
    text: str             # display text (romanized if Thanglish layer ran)
    native: str = ""      # original-script text (kept for reference/export)
    words: list[dict[str, Any]] = field(default_factory=list)
    # words: [{"t": "hello", "s": 1.02, "e": 1.31}, ...] record-timeline times


@dataclass
class Cutlist:
    source: str                                   # original media path
    duration: float                               # source duration, seconds
    fps: float = 30.0
    width: int = 0
    height: int = 0
    sample_rate: int = 48000
    tracks: dict[str, list[Clip]] = field(default_factory=dict)   # role → clips
    captions: list[CaptionCue] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)   # audit trail
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------- helpers
    def keep_ranges(self) -> list[tuple[float, float]]:
        """Ranges of the SOURCE timeline kept on the A-roll (video) track."""
        v = sorted(self.tracks.get("video", []), key=lambda c: c.rec_in)
        return [(c.src_in, c.src_out) for c in v]

    def rec_duration(self) -> float:
        return max((c.rec_in + c.duration for c in self.tracks.get("video", [])), default=0.0)

    # ------------------------------------------------------------- serde
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["version"] = CUTLIST_VERSION
        d["tracks"] = {k: [asdict(c) for c in v] for k, v in self.tracks.items()}
        d["captions"] = [asdict(c) for c in self.captions]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Cutlist":
        tracks = {k: [Clip(**c) for c in v] for k, v in d.get("tracks", {}).items()}
        caps = [CaptionCue(**c) for c in d.get("captions", [])]
        return cls(
            source=d["source"], duration=d["duration"],
            fps=d.get("fps", 30.0), width=d.get("width", 0), height=d.get("height", 0),
            sample_rate=d.get("sample_rate", 48000),
            tracks=tracks, captions=caps,
            dropped=d.get("dropped", []), meta=d.get("meta", {}),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "Cutlist":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # ------------------------------------------------------------- builders
    @classmethod
    def from_keep_ranges(
        cls,
        source: str,
        duration: float,
        keep: list[tuple[float, float]],
        **kw: Any,
    ) -> "Cutlist":
        """Build the A-roll track from (start, end) source ranges, packed
        back-to-back on the record timeline (jump cuts become separate clips)."""
        cl = cls(source=source, duration=duration, **kw)
        rec = 0.0
        clips = []
        for i, (s, e) in enumerate(sorted(keep)):
            if e - s <= 0:
                continue
            clips.append(Clip(media=source, src_in=s, src_out=e, rec_in=rec,
                              name=f"cut_{i:04d}", role="video"))
            rec += e - s
        cl.tracks["video"] = clips
        return cl
