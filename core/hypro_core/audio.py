"""Audio cleanup — one-click "mic → studio sound" (research/07).

v1 ships the ffmpeg-only chain from research/07 §7.2, in the order the
research doc insists on (§7.1: "denoise → level → EQ → loudness → limiter"):

    afftdn (FFT denoise) → highpass 78 Hz → acompressor (voice leveler)
    → deesser → firequalizer (gentle voice curve) → two-pass loudnorm
    → alimiter (true-peak safety)

Why ffmpeg-only: DeepFilterNet3 is the best OSS neural denoiser but its
wheels stop at Python 3.11 (research/07 §0 finding 2), so every model-based
denoiser is out of scope for v1. The classical chain is nearly free —
measured RTF ≈ 0.016, i.e. ~45 s per 40-minute podcast (research/07 §3) —
and needs zero extra Python dependencies.

Two-pass loudnorm is deliberate (research/07 §3): pass 1 measures the
*dynamics-processed* signal with `print_format=json` (measuring after the
compressor/EQ keeps the LUFS number truthful — §7.1), pass 2 applies a
single LINEAR gain with the `measured_*` params, which avoids the
pumping/breathing artifacts of loudnorm's single-pass dynamic mode.

DNSMOS auto-verification (research/07 §5) is a documented v1 non-goal: it
needs torch/onnxruntime, which the venv does not carry.
"""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from .media import probe_media

__all__ = ["enhance_ffmpeg", "analyze_loudness"]

# The pre-loudnorm half of the chain (research/07 §7.1 pass-2 command, exact
# params: afftdn nr/nf from §7.2, compressor/HPF from §3, voice curve from §3).
# firequalizer's gain expr must be single-quoted so the commas inside if()
# aren't read as filter separators by the filtergraph parser.
_PRE_CHAIN = (
    "afftdn=nr=15:nf=-45:tn=true,"
    "highpass=f=78,"
    "acompressor=threshold=-20dB:ratio=2.8:attack=20:release=250:makeup=4,"
    "deesser,"
    "firequalizer=gain='if(lt(f,110),-4,if(lt(f,3000),0,if(lt(f,6500),2.5,-1)))'"
)

# Podcast loudness target (research/07 §3): I=-16 LUFS, TP=-1.5 dBTP, LRA=11.
_DEFAULT_I, _DEFAULT_TP, _DEFAULT_LRA = -16.0, -1.5, 11.0

# Suffixes that must not receive a video stream (research/07 §7.1 Stage 0
# converts to plain audio for exactly these containers).
_AUDIO_ONLY_SUFFIXES = {".wav", ".flac", ".mp3", ".ogg", ".opus", ".m4a", ".aac"}
# Containers where a faststart remux helps progressive download.
_FASTSTART_SUFFIXES = {".mp4", ".m4v", ".mov"}


def _run_ffmpeg(cmd: list[str]) -> str:
    """Run an ffmpeg command, return its stderr.

    Raises RuntimeError with the tail of stderr on failure so the caller
    (or the user) sees the actual ffmpeg complaint, not just an exit code.
    """
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


def _measure(src: str, pre_chain: str, i: float, tp: float, lra: float) -> dict:
    """Loudnorm measurement pass (research/07 §3 "Pass 1: measure").

    Runs `pre_chain` (may be empty) followed by loudnorm with
    print_format=json and returns the parsed floats — including
    `target_offset`, which pass 2 feeds back as loudnorm's `offset`.
    """
    af = ",".join(
        p for p in (pre_chain,
                    f"loudnorm=I={i:g}:TP={tp:g}:LRA={lra:g}:print_format=json") if p
    )
    # NOTE: no -loglevel here. loudnorm prints its JSON block at INFO level,
    # so `-loglevel error` silently yields no JSON (verified on ffmpeg 9.0.2).
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", src,
           "-map", "0:a:0", "-af", af, "-f", "null", "-"]
    stderr = _run_ffmpeg(cmd)
    start, end = stderr.rfind("{"), stderr.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(
            f"loudnorm measurement produced no JSON for {src}:\n{stderr[-400:]}"
        )
    try:
        raw = json.loads(stderr[start:end + 1])
        return {key: float(raw[key]) for key in
                ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"unexpected loudnorm JSON for {src}: {stderr[start:end + 1][:300]}"
        ) from exc


def analyze_loudness(audio_or_video: str) -> dict:
    """Measure EBU R128 loudness of a media file (research/07 §3, pass 1 only).

    Returns {"input_i", "input_tp", "input_lra", "input_thresh"} as floats —
    integrated loudness (LUFS), true peak (dBTP), loudness range and the
    relative threshold. A silent input yields -inf for some fields, which is
    a valid float and the caller's cue that normalization can't help.

    Raises FileNotFoundError (missing file) or RuntimeError (no audio).
    """
    path = str(audio_or_video)
    info = probe_media(path)
    if not info.has_audio:
        raise RuntimeError(f"no audio stream in {path} — nothing to measure")
    m = _measure(path, "", _DEFAULT_I, _DEFAULT_TP, _DEFAULT_LRA)
    return {k: m[k] for k in ("input_i", "input_tp", "input_lra", "input_thresh")}


def _apply_chain(measured: dict | None, i: float, tp: float, lra: float,
                 rate: int) -> str:
    """Build the pass-2 -af chain: pre-chain → loudnorm → limiter → resample.

    loudnorm always emits 192 kHz (measured: the AAC encoder silently
    resampled to 96 kHz), so the original sample rate is pinned back at the
    end of the chain. `measured=None` falls back to single-pass dynamic
    loudnorm (research/07 §7.2's "single-pass costs quality but never fails").
    """
    ln = f"loudnorm=I={i:g}:TP={tp:g}:LRA={lra:g}"
    if measured is not None:
        ln += (f":measured_I={measured['input_i']:g}"
               f":measured_TP={measured['input_tp']:g}"
               f":measured_LRA={measured['input_lra']:g}"
               f":measured_thresh={measured['input_thresh']:g}"
               f":offset={measured['target_offset']:g}"
               ":linear=true")
    return ",".join([
        _PRE_CHAIN,
        ln,
        "alimiter=limit=0.97:level=false",
        f"aresample={rate}",
    ])


def enhance_ffmpeg(video: str, out: str, *, i: float = -16.0,
                   tp: float = -1.5, lra: float = 11.0) -> str:
    """Enhance a media file's audio in place of its audio track; video is
    stream-copied (`-c:v copy`) so the pass is fast and frame-identical.

    Chain (research/07 §7.1/§7.2): afftdn → highpass → acompressor → deesser
    → firequalizer → two-pass loudnorm (measure after dynamics, then apply
    linear) → alimiter. Duration is unchanged.

    Parameters `i`/`tp`/`lra` are the loudnorm targets (LUFS integrated,
    true peak, loudness range) — the podcast standard is -16 / -1.5 / 11;
    use i=-14 for YouTube-style loudness.

    Raises RuntimeError if the input has no audio stream or ffmpeg fails,
    ValueError if output would overwrite the input. Returns `out`.
    """
    src, dst = str(video), str(out)
    info = probe_media(src)
    if not info.has_audio:
        raise RuntimeError(
            f"no audio stream in {src} — enhance_ffmpeg needs audio to clean up"
        )
    if Path(src).resolve() == Path(dst).resolve():
        raise ValueError(f"output must differ from input (both are {src!r})")
    Path(dst).parent.mkdir(parents=True, exist_ok=True)

    # Pass 1 — measure AFTER the dynamics chain (research/07 §7.1: measure on
    # the enhanced signal so the target gain matches what pass 2 will see).
    m = _measure(src, _PRE_CHAIN, i, tp, lra)
    if not all(math.isfinite(m[k]) for k in
               ("input_i", "input_tp", "input_lra", "input_thresh")):
        # Digital-silence input: linear mode has no finite reference gain.
        m = None
    af = _apply_chain(m, i, tp, lra, info.sample_rate)

    suffix = Path(dst).suffix.lower()
    audio_only = suffix in _AUDIO_ONLY_SUFFIXES or not info.has_video
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src]
    if audio_only:
        cmd += ["-vn"]
    else:
        # Frame-identical remux of the video; only the audio is re-encoded.
        cmd += ["-map", "0:v:0", "-c:v", "copy"]
    cmd += ["-map", "0:a:0", "-af", af]
    if not audio_only:
        # webm cannot carry AAC — use the native Opus encoder there.
        cmd += (["-c:a", "opus", "-b:a", "192k"] if suffix == ".webm"
                else ["-c:a", "aac", "-b:a", "192k"])
        if suffix in _FASTSTART_SUFFIXES:
            cmd += ["-movflags", "+faststart"]
    cmd += [dst]

    _run_ffmpeg(cmd)
    if not Path(dst).exists():
        raise RuntimeError(f"ffmpeg reported success but {dst} was not created")
    return dst
