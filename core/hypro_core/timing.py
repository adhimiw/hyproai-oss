"""Word-clock transcript — the interface between ASR and every downstream stage.

One JSON file (``words.json``) carries the whole pipeline:

    transcribe (ASR)  →  Transcript
    analyze           →  reads .words  → Cutlist (keep ranges)
    captions          →  reads .words  → CaptionCue list → ASS
    export            →  reads CaptionCue → SRT

All times are floats in SECONDS on the SOURCE timeline (pre-cut). The
cutlist's CaptionCue times are on the RECORD timeline (post-cut); stages
that cross that boundary remap explicitly.

Schema (words.json):

    {
      "source": "/abs/path/video.mp4",
      "language": "ta",                 # BCP-47-ish ASR code (or null)
      "duration": 93.4,
      "fps": 30.0, "width": 1080, "height": 1920, "sample_rate": 48000,
      "engine": {"asr": "faster-whisper", "model": "small"},
      "words":  [{"t": "வணக்கம்", "s": 0.50, "e": 0.78, "conf": 0.94}],
      "segments": [{"start": 0.5, "end": 4.2, "text": "…"}]   # optional
    }
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Word:
    """One timed word on the source timeline."""

    t: str          # token exactly as ASR emitted it (native script or Latin)
    s: float        # start, seconds
    e: float        # end, seconds
    conf: float = 1.0  # ASR confidence 0..1 (1.0 when the engine gives none)

    @property
    def dur(self) -> float:
        return self.e - self.s


@dataclass
class Transcript:
    """The full word clock for one media file."""

    source: str
    duration: float
    language: str | None = None
    fps: float = 30.0
    width: int = 0
    height: int = 0
    sample_rate: int = 48000
    engine: dict[str, Any] = field(default_factory=dict)
    words: list[Word] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------- helpers
    def gaps(self, *, min_dur: float = 0.0) -> list[tuple[float, float]]:
        """Silent/unspeaking gaps BETWEEN words (source timeline).

        Leading/trailing silences are included (they are real dead air a
        silence remover should cut). ``min_dur`` filters tiny gaps out.
        """
        out: list[tuple[float, float]] = []
        if not self.words:
            if self.duration > 0:
                return [(0.0, self.duration)]
            return out
        ws = sorted(self.words, key=lambda w: w.s)
        if ws[0].s > 0:
            out.append((0.0, ws[0].s))
        for a, b in zip(ws, ws[1:]):
            g = (a.e, b.s)
            if g[1] - g[0] > min_dur:
                out.append(g)
        if ws[-1].e < self.duration:
            out.append((ws[-1].e, self.duration))
        return out

    def text(self) -> str:
        return " ".join(w.t for w in self.words)

    # ------------------------------------------------------------- serde
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["words"] = [asdict(w) for w in self.words]
        d["version"] = 1
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        return cls(
            source=d["source"],
            duration=float(d["duration"]),
            language=d.get("language"),
            fps=float(d.get("fps", 30.0)),
            width=int(d.get("width", 0)),
            height=int(d.get("height", 0)),
            sample_rate=int(d.get("sample_rate", 48000)),
            engine=d.get("engine", {}),
            words=[Word(**w) for w in d.get("words", [])],
            segments=d.get("segments", []),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "Transcript":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def remap_to_record(
    words: list[Word], keep: list[tuple[float, float]]
) -> list[Word]:
    """Map source-timeline words onto the record timeline defined by ``keep``
    ranges (sorted (src_in, src_out) pairs packed back-to-back).

    Words that fall in a dropped range are discarded (their audio is gone).
    A word straddling a cut boundary keeps only the surviving part.
    """
    out: list[Word] = []
    rec = 0.0
    for s_in, s_out in sorted(keep):
        span = s_out - s_in
        if span <= 0:
            continue
        for w in words:
            if w.e <= s_in or w.s >= s_out:
                continue
            ns = max(w.s, s_in) - s_in + rec
            ne = min(w.e, s_out) - s_in + rec
            if ne > ns:
                out.append(Word(t=w.t, s=ns, e=ne, conf=w.conf))
        rec += span
    return out
