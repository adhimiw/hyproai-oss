"""Stage A — word-level ASR: video → :class:`~hypro_core.timing.Transcript`.

The transcript ("word clock") is the single source of truth for every
downstream stage: `analyze` cuts on it, `captions` groups on it, `export`
remaps it. Getting word boundaries right here is the whole ball game —
research/05 §5: "timestamp quality, not WER, is our real dependency".

Engine choice (research/03 §2.1, §7.4): **faster-whisper** — MIT, ~4x faster
than openai/whisper on CPU, and `word_timestamps=True` gives native word
boundaries via cross-attention + DTW with **zero extra aligner models**.
WhisperX's forced alignment is sharper (93.2% vs 85.4% precision @200 ms,
research/03 §4.1) but needs one aligner per language and breaks on
code-switched audio (research/03 §4.3b) — v1 stays on the zero-dependency
path and keeps the aligner as a future drop-in.

Three settings carry their weight (all from the research docs):

* ``vad_filter=True`` with ``max_speech_duration_s=7`` — Whisper's 224-token
  decoder cap costs ~3 tokens per Indic character, so transcripts die at
  ~70-90 chars; VAD windows of ~6-7 s avoid the truncation
  (research/03 §4.3a).
* ``condition_on_previous_text=False`` — Whisper normalises disfluencies
  away and can hallucinate carry-over text; the filler stage needs "um/uh"
  to survive, which is exactly tightcut's documented trick
  (research/05 §1.4, §3.2 rule 1). Callers seed disfluencies further with
  ``initial_prompt``.
* device/compute auto-pick: cuda+float16 when a GPU is visible, else
  cpu+int8 (research/03 §2.1 speed table), with one whole-attempt CPU
  retry when the auto-picked CUDA stack fails — at model load *or* at the
  first decode, which is where a driver-present-but-runtime-missing
  machine (no ``libcublas.so.12``) actually dies — so a broken driver
  never fails the pipeline.

Confidence per word: faster-whisper emits a native per-word
``probability`` (mean token probability, research/03 §2.1 "per-word
confidence comes free") — we use it. Fallback chain when it is absent:
``exp(segment.avg_logprob)`` (crude but monotone map of the segment's
average log-probability into (0, 1]), else 1.0.
"""
from __future__ import annotations

import math
import os
import string
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from .media import probe_media
from .timing import Transcript, Word

# Scratch dir: prefer the harness-approved /tmp/opencode over /tmp (per env
# policy); fall back to the system temp dir elsewhere.
_SCRATCH_DIR = Path("/tmp/opencode")

# Strip this from ASR tokens before matching/normalising downstream.
_PUNCT = string.punctuation + "\u2026\u2018\u2019\u201c\u201d\u2013\u2014\u00b7"


def _scratch_dir() -> Path:
    """Directory for throwaway files (must exist — we never mkdir outside our scope)."""
    if _SCRATCH_DIR.is_dir():
        return _SCRATCH_DIR
    return Path(tempfile.gettempdir())


def extract_audio(video: str, out_wav: str | None = None, sr: int = 16000) -> str:
    """Decode ``video``'s audio to a mono PCM WAV and return the WAV path.

    16 kHz mono is Whisper's native input frontend (faster-whisper consumes
    the same 16 kHz mel pipeline as openai/whisper — research/03 §7.4), and a
    mono downmix keeps VAD/loudness statistics speaker-agnostic.

    ``out_wav=None`` writes a scratch file that **the caller owns** (delete it
    when done; `transcribe()` does this in a ``finally``). Passing ``out_wav``
    keeps the file — that is the "extraction requested explicitly" case.

    Raises:
        FileNotFoundError: ``video`` does not exist.
        RuntimeError: ffmpeg missing/failed (message carries ffmpeg's stderr
            tail so a missing-audio-stream input is immediately diagnosable).
    """
    if sr <= 0:
        raise ValueError(f"sr must be positive, got {sr}")
    if not Path(video).exists():
        raise FileNotFoundError(video)

    tmp_created = False
    if out_wav is None:
        fd, out_wav = tempfile.mkstemp(
            prefix="hypro_audio_", suffix=".wav", dir=str(_scratch_dir())
        )
        os.close(fd)
        tmp_created = True

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video),
        "-vn",                      # drop video: ASR only needs the audio
        "-ac", "1",                 # mono downmix
        "-ar", str(sr),             # Whisper expects 16 kHz
        "-c:a", "pcm_s16le",        # lossless PCM, cheap to decode
        str(out_wav),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        if tmp_created:
            Path(out_wav).unlink(missing_ok=True)
        raise RuntimeError("ffmpeg not found — install ffmpeg") from e
    if proc.returncode != 0:
        if tmp_created:
            Path(out_wav).unlink(missing_ok=True)
        raise RuntimeError(
            f"audio extraction failed for {video} (no audio stream?): "
            f"{proc.stderr.strip()[:400]}"
        )
    if not Path(out_wav).exists() or Path(out_wav).stat().st_size == 0:
        if tmp_created:
            Path(out_wav).unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg produced no audio data for {video}")
    return str(out_wav)


def _load_wav(path: str) -> np.ndarray:
    """Decode the extracted 16 kHz mono WAV to float32 for faster-whisper.

    We hand faster-whisper an ``ndarray`` rather than the file path on
    purpose: its path decoder (PyAV's ``av.open(..., metadata_errors=...)``)
    raises ``TypeError`` against the PyAV build installed in this venv, and
    reading back our own already-16 kHz mono PCM with soundfile is both
    exact and cheaper than re-decoding. faster-whisper skips its PyAV step
    entirely for ndarray input (``if not isinstance(audio, np.ndarray)``).
    """
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    if sr != 16000:
        # extract_audio() forces -ar 16000; anything else is a bug upstream.
        raise RuntimeError(f"expected 16 kHz WAV from extract_audio, got {sr} Hz")
    if data.size == 0:
        raise RuntimeError(f"extracted audio is empty: {path}")
    return data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]


def _cuda_available() -> bool:
    """True when ctranslate2 can see at least one CUDA device.

    Probing must never take the pipeline down: a broken/driver-mismatched
    CUDA stack is exactly the case where we want "False" (→ CPU), not a
    crash, so any probe failure degrades to CPU (skill guidance: "CUDA/
    ctranslate2 error → auto-falls back to CPU").
    """
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 — capability probe, failure == "no GPU"
        return False


def _resolve_device(device: str | None) -> tuple[str, str, bool]:
    """Pick ``(device, compute_type, fallback_allowed)``.

    * ``None``/``"auto"`` → cuda+float16 if a GPU is visible else cpu+int8,
      with CPU fallback allowed (auto mode must not fail on a flaky driver).
    * Explicit ``"cpu"``/``"cuda"`` → honored exactly; failure raises later
      (PIPELINE style: fail loudly rather than silently changing the
      caller's requested hardware).
    """
    if device in (None, "auto"):
        if _cuda_available():
            return "cuda", "float16", True
        return "cpu", "int8", False
    d = str(device).lower()
    if d == "cuda":
        return "cuda", "float16", False
    if d == "cpu":
        return "cpu", "int8", False
    raise ValueError(f"unsupported device {device!r}: expected 'cpu', 'cuda', 'auto' or None")


def _load_model(name: str, device: str, compute_type: str):
    """Construct the ``WhisperModel`` for ``device`` (no internal fallback).

    The cuda → cpu retry lives in :func:`transcribe`, around a *whole*
    attempt, because ctranslate2 fails at two different points: model
    construction, and the first decode (a machine with a visible GPU but no
    CUDA runtime libs — driver present, ``libcublas.so.12`` missing — loads
    fine and only blows up inside ``transcribe()``). A load-time-only
    fallback would never see that case.
    """
    try:
        from faster_whisper import WhisperModel

        return WhisperModel(name, device=device, compute_type=compute_type)
    except Exception as e:  # noqa: BLE001 — model-load errors are many-typed
        raise RuntimeError(
            f"could not load Whisper model {name!r} "
            f"(device={device!r}, compute_type={compute_type!r}): {e}"
        ) from e


def _word_conf(word: object, segment: object) -> float:
    """Per-word confidence 0..1.

    Primary: faster-whisper's native ``Word.probability`` (mean token
    probability for the word — research/03 §2.1, free with
    ``word_timestamps=True``). Fallback: ``exp(segment.avg_logprob)``, a
    crude but monotone map of the segment's average log-probability into
    (0, 1] (avg_logprob is <= 0; -0.2 → ~0.82, -1.0 → ~0.37). Last resort:
    1.0, the documented "engine gave no confidence" value (timing.Word).
    """
    p = getattr(word, "probability", None)
    if isinstance(p, (int, float)) and 0.0 <= p <= 1.0:
        return float(p)
    lp = getattr(segment, "avg_logprob", None)
    if isinstance(lp, (int, float)):
        return min(1.0, max(0.0, math.exp(float(lp))))
    return 1.0


def _run_asr(
    audio: np.ndarray,
    *,
    video: str,
    model: str,
    device: str,
    compute_type: str,
    tw_kwargs: dict,
    media_duration: float,
    word_timestamps: bool,
) -> tuple[list[Word], list[dict], str, float]:
    """One complete ASR attempt on ``device``: load → decode → consume.

    Load *and* inference sit inside a single call so the caller's retry loop
    has one failure boundary to retry from — ctranslate2 can raise at either
    point (missing ``libcublas.so.12`` typically fails on the first decode,
    not on construction).

    Returns ``(words, segments, language, duration)`` with words clamped to
    the source timeline and sorted.
    """
    asr = _load_model(model, device, compute_type)
    try:
        seg_iter, tw_info = asr.transcribe(audio, **tw_kwargs)
    except Exception as e:  # noqa: BLE001 — surface as a readable stage error
        raise RuntimeError(
            f"faster-whisper failed to start on {video} "
            f"(model={model!r}, device={device}): {e}"
        ) from e

    duration = media_duration if media_duration > 0 else float(tw_info.duration)
    language = tw_kwargs.get("language") or tw_info.language

    words: list[Word] = []
    segments: list[dict] = []
    try:
        for seg in seg_iter:
            segments.append(
                {"start": float(seg.start), "end": float(seg.end),
                 "text": seg.text}
            )
            if not word_timestamps:
                continue
            for w in seg.words or []:
                token = (w.word or "").strip()
                if not token:
                    continue
                # Clamp to the source timeline; drop degenerate spans
                # (zero/negative length would break every downstream math).
                s = min(max(float(w.start), 0.0), duration)
                e = min(max(float(w.end), 0.0), duration)
                if e <= s:
                    continue
                words.append(Word(t=token, s=s, e=e, conf=_word_conf(w, seg)))
    except Exception as e:  # noqa: BLE001 — generator errors surface here
        raise RuntimeError(
            f"ASR transcription failed for {video} "
            f"(model={model!r}, device={device}): {e}"
        ) from e
    words.sort(key=lambda w: (w.s, w.e))
    return words, segments, str(language), duration


def transcribe(
    video: str,
    *,
    lang: str | None = None,
    model: str = "small",
    word_timestamps: bool = True,
    device: str | None = None,
    initial_prompt: str | None = None,
) -> Transcript:
    """Run ASR on ``video`` and return a word-timed :class:`Transcript`.

    Flow: ffprobe meta (:func:`probe_media`) → extract 16 kHz mono WAV →
    faster-whisper with Silero VAD on → consume segments → build Transcript.

    Args:
        video: source media path (needs an audio stream).
        lang: force a language code (``"ta"``, ``"hi"``, …); ``None`` lets
            faster-whisper auto-detect (result lands in ``tr.language``).
        model: faster-whisper model size/path (``small`` is cached here;
            research/05 §5: ``medium`` for harder Indic accents).
        word_timestamps: keep False only for segment-level work — analyze,
            captions and retakes all need the word clock.
        device: ``None``/``"auto"`` (GPU if present, CPU fallback),
            ``"cpu"`` or ``"cuda"`` (explicit requests fail loudly).
        initial_prompt: verbatim seed text — pass filler tokens
            ("um, uh, like, wait, …") so Whisper keeps disfluencies for the
            filler stage (research/05 §1.4, §3.2 rule 1).

    Returns:
        Transcript with ``engine={"asr": "faster-whisper", "model": model}``
        and ``language``/``fps``/``width``/``height``/``sample_rate`` filled.

    Raises:
        FileNotFoundError: ``video`` missing.
        RuntimeError: no audio stream, extraction failure, or every
            attempted device failed (the message lists each attempt's
            device/compute type and its error).
        ValueError: unsupported explicit ``device``.
    """
    path = Path(video)
    if not path.exists():
        raise FileNotFoundError(video)
    media = probe_media(str(path))
    if not media.has_audio:
        raise RuntimeError(f"{video}: no audio stream — nothing to transcribe")

    # Resolve the device BEFORE any file I/O so a bad request (ValueError)
    # cannot strand a temp WAV on disk.
    dev, compute_type, fallback = _resolve_device(device)
    wav = extract_audio(str(path))
    try:
        audio = _load_wav(wav)
        tw_kwargs: dict = {
            "language": lang,               # None → auto-detect (info.language)
            "word_timestamps": word_timestamps,
            "vad_filter": True,             # Silero VAD: skip music/silence
            "vad_parameters": {
                # research/03 §7.4 / §4.3(a): ~6-7 s windows keep Indic text
                # under Whisper's 224-token decoder cap (else transcripts
                # truncate at ~70-90 chars with U+FFFD).
                "max_speech_duration_s": 7.0,
                "min_silence_duration_ms": 500,   # research/05 §1.6: 400-600 ms
                "speech_pad_ms": 300,             # preserve word onsets
            },
            # research/05 §1.4/§3.2: don't condition on previous text — it
            # lets Whisper normalise away fillers and drift into hallucinated
            # carry-over; the filler stage depends on verbatim output.
            "condition_on_previous_text": False,
            # Scalar temperature = one deterministic beam pass (beam=5).
            # The default ladder (0.0..1.0, best_of=5) re-samples with
            # ctranslate2's per-process random seed whenever greedy trips
            # the compression/no-speech thresholds, so the SAME audio yields
            # transcripts, empty output, or an alignment IndexError
            # depending on the process (observed: 0 vs 14 segments).
            # Scalar keeps the greedy result in every case (faster-whisper's
            # all-failed branch selects it) and is reproducible run to run.
            "temperature": 0.0,
        }
        if initial_prompt is not None:
            tw_kwargs["initial_prompt"] = initial_prompt

        # Auto-picked GPU gets one CPU retry (research/03 §1: "CPU must work"
        # — a broken/incomplete CUDA stack must degrade, not fail the run);
        # explicit device requests are honoured exactly and raise.
        attempts: list[tuple[str, str]] = [(dev, compute_type)]
        if fallback and dev != "cpu":
            attempts.append(("cpu", "int8"))

        words: list[Word] = []
        segments: list[dict] = []
        language: str | None = lang
        duration = float(media.duration)
        failures: list[str] = []
        for i, (attempt_dev, attempt_ct) in enumerate(attempts):
            try:
                words, segments, language, duration = _run_asr(
                    audio,
                    video=str(path),
                    model=model,
                    device=attempt_dev,
                    compute_type=attempt_ct,
                    tw_kwargs=tw_kwargs,
                    media_duration=float(media.duration),
                    word_timestamps=word_timestamps,
                )
                break
            except Exception as e:  # noqa: BLE001 — one readable retry boundary
                failures.append(f"{attempt_dev}/{attempt_ct}: {e}")
                if i + 1 == len(attempts):
                    raise RuntimeError(
                        f"ASR failed for {video} (model={model!r}) after "
                        f"{len(attempts)} attempt(s):\n  - "
                        + "\n  - ".join(failures)
                    ) from e

        return Transcript(
            source=str(path.resolve()),
            duration=duration,
            language=language,
            fps=media.fps,
            width=media.width,
            height=media.height,
            sample_rate=media.sample_rate,
            engine={"asr": "faster-whisper", "model": model},
            words=words,
            segments=segments,
        )
    finally:
        # Scratch WAV is ours — always remove it (an explicit out_wav passed
        # to extract_audio() is the caller's, and that path never lands here).
        Path(wav).unlink(missing_ok=True)
