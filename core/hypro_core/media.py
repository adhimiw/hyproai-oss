"""ffprobe wrapper — one function every stage uses for media metadata."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MediaInfo:
    path: str
    duration: float
    fps: float
    width: int
    height: int
    sample_rate: int
    has_audio: bool
    has_video: bool


def probe_media(path: str | Path) -> MediaInfo:
    """Probe a media file with ffprobe. Raises FileNotFoundError/RuntimeError
    with a readable message on failure."""
    path = str(path)
    if not Path(path).exists():
        raise FileNotFoundError(path)
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        raw = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as e:
        raise RuntimeError("ffprobe not found — install ffmpeg") from e
    if raw.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {raw.stderr.strip()[:400]}")
    data = json.loads(raw.stdout)

    duration = float(data.get("format", {}).get("duration") or 0.0)
    fps, width, height = 30.0, 0, 0
    sample_rate, has_audio, has_video = 0, False, False
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and not has_video:
            has_video = True
            width = int(st.get("width") or 0)
            height = int(st.get("height") or 0)
            rate = st.get("avg_frame_rate") or st.get("r_frame_rate") or "30/1"
            num, _, den = rate.partition("/")
            try:
                fps = float(num) / float(den or 1) if float(den or 1) else 30.0
            except (ValueError, ZeroDivisionError):
                fps = 30.0
            if not duration:
                duration = float(st.get("duration") or 0.0)
        elif st.get("codec_type") == "audio" and not has_audio:
            has_audio = True
            sample_rate = int(st.get("sample_rate") or 48000)
    if not has_video and not has_audio:
        raise RuntimeError(f"no audio or video stream in {path}")
    return MediaInfo(
        path=path, duration=duration, fps=fps, width=width, height=height,
        sample_rate=sample_rate or 48000, has_audio=has_audio,
        has_video=has_video,
    )
