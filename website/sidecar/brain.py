"""Gemini brain adapter: `gemini-3.5-transcribe` + automatic local fallback.

Contract (API_CONTRACT.md §Gemini brain): "**Any failure** falls back to local
faster-whisper and emits a `log` event — never fails the job for brain
reasons." So :func:`transcribe` only raises when the *local* path fails too —
that is a real pipeline failure, not a brain choice.

Proven reference code (both checked in, both run against the live API):
- ``scripts/probe_transcribe_tamil.py`` — Files API resumable upload (needs
  ``X-Goog-Upload-Offset: 0``) + interactions call; research/12 verified
  Tamil at script-ratio 1.00 with ``word_info`` offsets 0.100s→8.200s in an
  8.4 s clip, and that the unlisted ``ta-IN`` hint is accepted.
- ``scripts/gemini_export_experiment.py`` — retry/backoff honoring the API's
  ``"retry in Ns"`` 429 hints (free tier rate limits are explicit).

Language (research/12 §verdict): user hint → BCP-47 map (``hi→hi-IN`` …
``bn→bn-IN``; ``ta-IN`` is missing from the docs table but accepted); no hint
→ ``language_codes: []`` (auto-detect, handles code-switching) and the
transcript's language is decided after the fact from the returned text by
``hypro_core.romanize.detect_lang``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_CORE = str(_HERE.parents[1] / "core")          # website/sidecar → <repo>/core
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from hypro_core.media import probe_media                     # noqa: E402
from hypro_core.romanize import detect_lang                  # noqa: E402
from hypro_core.timing import Transcript, Word               # noqa: E402

#: The contract's exact model id (API_CONTRACT.md body example).
GEMINI_MODEL = "models/gemini-3.5-transcribe"

#: ISO-639-1 hint → BCP-47 for `language_codes` (API_CONTRACT.md; research/12
#: confirmed ``ta-IN`` works even though the docs table omits it).
LANG_TO_BCP47: dict[str, str] = {
    "hi": "hi-IN", "ta": "ta-IN", "te": "te-IN",
    "ml": "ml-IN", "kn": "kn-IN", "bn": "bn-IN",
}

#: Every non-2xx we retry on, plus the free-tier 429 (research/12).
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)

_Emit = Callable[[str], None]


def _noop(_msg: str) -> None:
    """Default emit — unit tests and dry runs pass no SSE sink."""


# ------------------------------------------------------------ language hints
def language_codes(lang: str | None) -> list[str]:
    """BCP-47 hint list for ``transcription_config.language_codes``.

    ``[]`` means auto-detect (API_CONTRACT.md). An unknown hint fails loudly
    here rather than being silently downgraded to auto — a user who asked for
    Tamil must not get Hindi.
    """
    if lang is None:
        return []
    try:
        return [LANG_TO_BCP47[lang]]
    except KeyError as err:
        raise ValueError(
            f"unsupported language hint {lang!r}: expected one of "
            f"{sorted(LANG_TO_BCP47)}") from err


# ------------------------------------------------------- response → Word[]
def parse_content(content: Mapping[str, Any]) -> list[Word]:
    """Parse one Gemini ``model_output`` text item into timed :class:`Word`.

    Contract parse rule: ``annotations[]`` where ``type == "word_info"`` →
    ``{text, start_offset, end_offset}`` whose values look like ``"0.100s"``
    (``float(v.rstrip("s"))``). Other annotation types are not word timings
    and are ignored; a *malformed* ``word_info`` raises instead of being
    dropped — a truncated transcript silently handed downstream would cut the
    wrong video, so the caller falls back to local ASR instead.
    """
    words: list[Word] = []
    for ann in content.get("annotations") or []:
        if not isinstance(ann, Mapping) or ann.get("type") != "word_info":
            continue
        text = str(ann.get("text") or "")
        if not text:
            raise ValueError(f"word_info annotation missing text: {ann!r}")
        try:
            start = float(str(ann["start_offset"]).rstrip("s"))
            end = float(str(ann["end_offset"]).rstrip("s"))
        except KeyError as err:
            raise ValueError(
                f"word_info annotation missing {err.args[0]}: {ann!r}") from err
        except (TypeError, ValueError) as err:
            raise ValueError(
                f"word_info annotation has unparsable offsets: {ann!r}") from err
        if end < start:
            raise ValueError(
                f"word_info annotation ends before it starts: {ann!r}")
        words.append(Word(t=text, s=start, e=end, conf=1.0))
    return words


# ------------------------------------------------------------- raw HTTP I/O
def _http(url: str, *, data: bytes | None = None,
          headers: dict[str, str] | None = None, method: str | None = None,
          timeout: int = 180) -> tuple[int, dict[str, str], bytes]:
    """One request → ``(status, headers, body)``; HTTP errors come back as
    data (429 bodies carry the ``retry in Ns`` hint we must read)."""
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def _retry_hint(message: str) -> float | None:
    """``"Please retry in 8s"`` → ``8.0`` (free-tier 429s state the wait)."""
    match = re.search(r"retry in (\d+)s", message)
    return float(match.group(1)) if match else None


# ---------------------------------------------------------- Gemini pipeline
def _extract_mp3(video: str, out: Path) -> None:
    """ffmpeg-extract ``audio.mp3`` — 16 kHz mono 64k (API_CONTRACT.md flow;
    same flags as ``scripts/probe_transcribe_tamil.py``)."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video),
           "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(out)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except FileNotFoundError as err:
        raise RuntimeError("ffmpeg not found — cannot extract audio") from err
    except subprocess.CalledProcessError as err:
        raise RuntimeError(
            "ffmpeg audio extraction failed: "
            + err.stderr.decode(errors="replace")[:300]) from err


def upload_audio(mp3: Path, key: str) -> str:
    """Files API resumable upload → file URI (probe_transcribe_tamil.py).

    Two steps: ``start`` returns an upload URL, then ``upload, finalize``
    sends the bytes — the contract calls out that the second request needs
    ``X-Goog-Upload-Offset: 0``. Files can land in ``PROCESSING``; poll until
    ``ACTIVE`` before the interactions call.
    """
    size = mp3.stat().st_size
    status, headers, body = _http(
        "https://generativelanguage.googleapis.com/upload/v1beta/files",
        data=json.dumps({"file": {"display_name": mp3.name}}).encode(),
        headers={
            "x-goog-api-key": key,
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": "audio/mp3",
        },
        method="POST",
    )
    if status != 200:
        raise RuntimeError(f"gemini upload start: HTTP {status}: "
                           + body.decode(errors="replace")[:300])
    upload_url = headers.get("X-Goog-Upload-URL") or headers.get(
        "x-goog-upload-url")
    if not upload_url:
        raise RuntimeError(f"gemini upload start: no upload URL in headers "
                           f"({sorted(headers)})")

    status, _, body = _http(
        upload_url,
        data=mp3.read_bytes(),
        headers={
            "x-goog-api-key": key,
            "Content-Type": "application/octet-stream",
            "X-Goog-Upload-Command": "upload, finalize",
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": "audio/mp3",
        },
        method="POST",
    )
    if status != 200:
        raise RuntimeError(f"gemini upload finalize: HTTP {status}: "
                           + body.decode(errors="replace")[:300])
    uri = json.loads(body)["file"]["uri"]

    name = uri.rsplit("/", 1)[-1]
    for _ in range(30):                       # PROCESSING → ACTIVE, usually 0 s
        st, _, raw = _http(f"https://generativelanguage.googleapis.com/"
                           f"v1beta/files/{name}",
                           headers={"x-goog-api-key": key})
        state = json.loads(raw).get("state", "ACTIVE") if st == 200 else "ACTIVE"
        if state == "ACTIVE":
            return uri
        if state == "FAILED":
            raise RuntimeError(f"gemini file processing FAILED: "
                               + raw.decode(errors="replace")[:300])
        time.sleep(2)
    return uri                                # ACTIVE never confirmed: try anyway


def call_gemini(uri: str, key: str, codes: list[str]) -> dict[str, Any]:
    """One interactions call → the ``type=="text"`` model_output content item.

    Verbatim mode with word granularities (API_CONTRACT.md body). Retries 6
    times, honoring the server's ``"retry in Ns"`` hint over exponential backoff.
    """
    body = {
        "model": GEMINI_MODEL,
        "input": [{"type": "audio", "uri": uri, "mime_type": "audio/mp3"}],
        "generation_config": {"transcription_config": {
            "mode": {"type": "verbatim",
                     "timestamp_granularities": ["word"]},
            "language_codes": codes,
        }},
        "store": False,
    }
    last = ""
    for attempt in range(6):
        status, _, raw = _http(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            data=json.dumps(body).encode(),
            headers={"x-goog-api-key": key, "Content-Type": "application/json",
                     "Api-Revision": "2026-05-20"},
            method="POST", timeout=300,
        )
        if status == 200:
            resp = json.loads(raw)
            for step in resp.get("steps", []):
                if step.get("type") == "model_output":
                    for item in step.get("content", []):
                        if item.get("type") == "text":
                            return item
            raise RuntimeError("gemini interactions: no model_output text "
                               f"(top-level keys={sorted(resp)})")
        detail = raw.decode(errors="replace")[:400]
        last = f"gemini interactions: HTTP {status}: {detail}"
        if status in _RETRYABLE_STATUS:
            wait = max(_retry_hint(detail) or 0.0,
                       min(3 * 2 ** attempt, 60))
            time.sleep(wait)
            continue
        break                                 # 4xx that retrying won't fix
    raise RuntimeError(last)


def gemini_transcribe(video: str, *, lang: str | None,
                      key: str) -> Transcript:
    """Full Gemini path: extract → upload → transcribe → parse → Transcript.

    Raises on any step — :func:`transcribe` converts every one of these into
    a fallback + ``log`` event (contract: never fail the job for brain
    reasons).
    """
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    codes = language_codes(lang)              # validates the hint loudly
    with tempfile.TemporaryDirectory(prefix="hypro-gemini-") as tmp:
        mp3 = Path(tmp) / "audio.mp3"
        _extract_mp3(video, mp3)
        uri = upload_audio(mp3, key)
        content = call_gemini(uri, key, codes)

    words = parse_content(content)            # raises on malformed word_info
    if not words:
        raise RuntimeError("gemini returned no word_info annotations")
    full_text = str(content.get("text") or "") or " ".join(w.t for w in words)
    # Contract: language = hint if given else detect_lang(full_text).
    language = lang if lang is not None else detect_lang(full_text)

    info = probe_media(video)
    return Transcript(
        source=str(video),
        duration=float(info.duration),
        language=language,
        fps=info.fps,
        width=info.width,
        height=info.height,
        sample_rate=info.sample_rate,
        engine={"asr": GEMINI_MODEL, "brain": "gemini"},
        words=words,
    )


def local_transcribe(video: str, *, lang: str | None,
                     model: str) -> Transcript:
    """Local faster-whisper (the fallback, and ``brain: "local"`` itself).

    Import is lazy so the Gemini path never pays ctranslate2's import cost —
    the website's default brain should start fast even on machines that only
    ever call the API.
    """
    from hypro_core.transcribe import transcribe as local_tr

    transcript = local_tr(str(video), lang=lang, model=model,
                          word_timestamps=True)
    transcript.engine["brain"] = "local"      # stats report the brain USED
    return transcript


# --------------------------------------------------------------- dispatcher
def transcribe(video: str, *, lang: str | None = None,
               model: str = "small", brain: str = "gemini",
               emit: _Emit | None = None) -> Transcript:
    """Transcribe ``video`` with the requested brain.

    ``brain="gemini"`` tries the API and, on **any** failure (no key, upload
    error, rate limit, malformed response, empty transcript), emits one
    ``log`` event — the exact contract wording — and returns the local
    faster-whisper result instead. The job only fails if local ASR fails too.

    Args:
        video: source media path (needs an audio stream).
        lang: ``hi|ta|te|ml|kn|bn`` hint, or ``None`` for auto-detect.
        model: faster-whisper size for the local path (``tiny`` in tests).
        brain: ``"gemini"`` or ``"local"`` (validated by ``app.validate_options``).
        emit: sink for the fallback ``log`` event (pipeline wires SSE here).

    Returns:
        A :class:`Transcript` whose ``engine["brain"]`` names the engine that
        actually ran — the result card must not claim Gemini when the job
        silently used local ASR.

    Raises:
        ValueError: unknown ``brain`` name or unsupported language hint.
        RuntimeError: local ASR failed (after any Gemini attempt), or the
            Gemini path failed *before* fallback was possible (bad hint).
    """
    note = emit or _noop
    if brain == "gemini":
        try:
            key = os.environ.get("GEMINI_API_KEY", "").strip()
            return gemini_transcribe(video, lang=lang, key=key)
        except Exception as err:              # noqa: BLE001 — contract: ANY failure
            # Bad hints fail here too, but local ASR takes ISO-639-1 directly
            # and app.validate_options already vetted `lang`, so the fallback
            # is still the right move — and the log event keeps it honest.
            note(f"gemini unavailable ({err}) — falling back to local whisper")
    elif brain != "local":
        raise ValueError(f"unknown brain {brain!r}: expected 'gemini' or 'local'")
    return local_transcribe(video, lang=lang, model=model)


__all__ = ["GEMINI_MODEL", "LANG_TO_BCP47", "call_gemini", "gemini_transcribe",
           "language_codes", "local_transcribe", "parse_content", "transcribe",
           "upload_audio"]
