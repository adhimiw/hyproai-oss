"""FastAPI sidecar — the HTTP surface of the hypro website stage.

Every route, field name, status code and SSE event here follows
``website/API_CONTRACT.md`` literally (any mismatch is a bug in this file,
not in the doc):

    GET  /health                     liveness + gemini_key presence
    GET  /presets                    the 8 caption presets (core is source of truth)
    POST /uploads                    multipart video → 201 upload record
    POST /jobs                       validated job request → 202 + background thread
    GET  /jobs/{id}                  status/stage/percent/message/error/result
    GET  /jobs/{id}/events           SSE: replay stored log, then live, ~15 s pings
    GET  /jobs/{id}/files/{name}     the six whitelisted artifacts

Layout rules (contract): the core is imported by inserting ``<repo>/core``
into ``sys.path`` (works locally and in Docker with no env vars); data root
is ``HYPRO_DATA_DIR`` else ``<repo>/data``; CORS is ``allow_origins=["*"]``
so the UI may bypass the Next proxy.

Errors are always ``{"error": "<human message>"}`` — handled centrally by
the exception handlers below so 4xx and unhandled 5xx never leak starlette's
``{"detail": ...}`` shape.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, BinaryIO

# Pipeline events are mirrored to stdout by jobs.py so `docker logs -f` is a
# real backend log. uvicorn only configures its own loggers, so the root
# logger needs a handler before the first job runs (no-op if one exists).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)

# Bootstrap: sibling modules (jobs, pipeline) and the core package.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_CORE = str(_HERE.parents[1] / "core")           # website/sidecar → <repo>/core
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from fastapi import FastAPI, HTTPException, Request          # noqa: E402
from fastapi.exceptions import RequestValidationError        # noqa: E402
from fastapi.middleware.cors import CORSMiddleware           # noqa: E402
from fastapi.responses import FileResponse, JSONResponse      # noqa: E402
from fastapi.responses import StreamingResponse              # noqa: E402
from starlette.requests import ClientDisconnect              # noqa: E402

import jobs                                                  # noqa: E402
import pipeline                                              # noqa: E402
from hypro_core.captions import list_presets                 # noqa: E402
from hypro_core.media import probe_media                     # noqa: E402

#: Contract's health payload version string.
VERSION = "0.1.0"

#: Upload caps (API_CONTRACT.md POST /uploads).
MAX_UPLOAD_BYTES = 500 * 1024 * 1024      # ≤ 500 MB else 413
MAX_UPLOAD_SECONDS = 600.0                # ≤ 600 s  else 413

#: Read/write granularity for the streaming copy (keeps RAM flat at 1 MiB).
CHUNK = 1024 * 1024

#: SSE heartbeat interval (contract: "Heartbeat comment `: ping` every ~15 s").
HEARTBEAT_SECONDS = 15.0

#: ``GET /jobs/{id}/files/{name}`` whitelist → Content-Type (contract).
ALLOWED_FILES: dict[str, str] = {
    "edited.mp4": "video/mp4",
    "captions.srt": "text/plain; charset=utf-8",
    "captions.ass": "text/plain; charset=utf-8",
    "cutlist.json": "application/json",
    "words.json": "application/json",
    "nle.zip": "application/zip",
}

#: POST /jobs options defaults; the UI's toggles all default per contract.
DEFAULT_OPTIONS: dict[str, Any] = {
    "silence": True, "retake": False, "fillers": False,
    "burn": True, "enhance_audio": True,
    "brain": "gemini", "model": "small",
}
_BOOL_OPTIONS = ("silence", "retake", "fillers", "burn", "enhance_audio")
_LANGS = ("hi", "ta", "te", "ml", "kn", "bn")


class UploadTooLarge(Exception):
    """Size or duration over the contract cap → HTTP 413."""


class UploadInvalid(Exception):
    """Unusable upload (no video stream, unprobeable) → HTTP 400."""


# ----------------------------------------------------------------- helpers
def copy_limited(src: BinaryIO, dest: str | Path, max_bytes: int) -> int:
    """Stream ``src`` → ``dest`` in :data:`CHUNK` slices, refusing past ``max_bytes``.

    Why chunked and never ``read()`` whole: a 500 MB upload must not be
    buffered in RAM (contract cap), and the same routine is the unit-tested
    home of the size rule — starlette's multipart parser enforces the cap
    first, this is the second lock on the same door.
    """
    written = 0
    with open(dest, "wb") as out:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise UploadTooLarge(
                    f"file exceeds the {max_bytes // (1024 * 1024)} MB upload limit")
            out.write(chunk)
    return written


def check_duration(duration: float, limit: float = MAX_UPLOAD_SECONDS) -> None:
    """Contract: media longer than 600 s is rejected with 413."""
    if duration > limit:
        raise UploadTooLarge(
            f"media duration {duration:.1f}s exceeds the {limit:g}s limit")


def safe_suffix(filename: str | None) -> str:
    """``input.<ext>`` extension from the client filename.

    Only ``[a-z0-9]{1,8}`` suffixes survive (so ``../../evil`` or ``x.php``
    can't shape the stored path); anything else becomes ``.mp4`` — the file
    content is ffprobe-validated anyway, the extension is a label.
    """
    suffix = Path(filename or "").suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".mp4"


def validate_options(raw: dict[str, Any]) -> dict[str, Any]:
    """Fill contract defaults and type-check every option → 400s on bad input.

    Unknown option keys are ignored on purpose: the web UI may add toggles
    before the sidecar learns about them, and silently forwarding them to
    this v1 schema would break re-renders.
    """
    if not isinstance(raw, dict):
        raise HTTPException(400, "options must be a JSON object")
    opts = dict(DEFAULT_OPTIONS)
    for key, value in raw.items():
        if key in _BOOL_OPTIONS:
            if not isinstance(value, bool):
                raise HTTPException(
                    400, f"options.{key} must be true or false, got {value!r}")
            opts[key] = value
        elif key == "brain":
            if value not in ("gemini", "local"):
                raise HTTPException(
                    400, f"options.brain must be 'gemini' or 'local', got {value!r}")
            opts["brain"] = value
        elif key == "model":
            if not isinstance(value, str) or not value.strip():
                raise HTTPException(
                    400, f"options.model must be a whisper model name, got {value!r}")
            opts["model"] = value.strip()
    return opts


# ------------------------------------------------------------------- app
app = FastAPI(title="hypro sidecar", version=VERSION)
# Contract requirement: CORS open so the UI may bypass the Next proxy if
# SSE-through-rewrite ever misbehaves.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

STORE = jobs.JobStore()
UPLOADS = jobs.UploadStore()


# --------------------------------------------------------- error envelopes
@app.exception_handler(HTTPException)
async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    """Contract: every 4xx/5xx body is ``{"error": "<human message>"}``."""
    return JSONResponse(status_code=exc.status_code,
                        content={"error": str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request,
                            exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422/detail shape would violate the envelope —
    rewrite as a readable 400 (missing multipart field, bad query type …)."""
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", ())) or "request"
    return JSONResponse(status_code=400,
                        content={"error": f"invalid request: {where}: {first.get('msg', 'bad input')}"})


@app.exception_handler(Exception)
async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort envelope for 500s — log with traceback for ops, never
    leak a stack trace to the client."""
    import logging

    logging.getLogger("hypro.sidecar").exception(
        "unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500,
                        content={"error": f"internal error: {type(exc).__name__}: {exc}"})


# ------------------------------------------------------------------ health
@app.get("/health")
def health() -> dict[str, Any]:
    """Contract shape: ``{"ok": true, "gemini_key": true, "version": "0.1.0"}``."""
    return {
        "ok": True,
        "gemini_key": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
        "version": VERSION,
    }


@app.get("/presets")
def presets() -> dict[str, Any]:
    """All caption presets: the full JSON from
    ``core/hypro_core/caption_presets/*.json`` (source of truth, ``id`` =
    filename stem) plus the flat ``mode``/``font``/``size`` convenience keys
    API_CONTRACT.md's example shows, so the picker can read either shape."""
    out: list[dict[str, Any]] = []
    for pid in list_presets():
        path = _HERE.parents[1] / "core" / "hypro_core" / "caption_presets" / f"{pid}.json"
        preset = json.loads(path.read_text(encoding="utf-8"))
        preset.setdefault("id", pid)
        preset["mode"] = preset["animation"]["mode"]
        preset["font"] = preset["typography"]["fontFamily"]
        preset["size"] = preset["typography"]["fontSize"]
        out.append(preset)
    return {"presets": out}


# ----------------------------------------------------------------- uploads
async def _parse_upload_form(request: Request):
    """Parse the multipart body with the parser's part cap set to OUR 500 MB
    limit, and translate starlette's oversize rejection onto the contract's
    413.

    starlette ≥1 caps each part at ``max_part_size`` (default 1 MB — far too
    small for video) and converts its ``MultiPartException`` into HTTP 400;
    the contract requires 413 for oversize, so the cap is raised to exactly
    our limit and its message re-mapped. Anything ≤500 MB reaches
    :func:`copy_limited`, which re-checks the same rule.
    """
    try:
        return await request.form(max_files=8, max_fields=8,
                                  max_part_size=MAX_UPLOAD_BYTES)
    except ClientDisconnect:
        # The client went away mid-upload (user cancelled, proxy dropped it).
        # Not a server error: re-raise so nothing is stored, but keep the
        # 500 envelope from claiming "internal error" (it isn't one).
        raise HTTPException(499, "client disconnected during upload")
    except HTTPException as e:
        if "exceeded maximum size" in str(e.detail).lower():
            raise HTTPException(
                413, f"file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
                     "upload limit") from e
        raise


@app.post("/uploads", status_code=201)
async def create_upload(request: Request) -> dict[str, Any]:
    """``multipart/form-data`` field ``file`` → 201 upload record.

    Limits, in contract order: ≤500 MB (413), ≤600 s (413), must have a
    video stream (400). Stores ``uploads/<uuid>/input.<ext>``.
    """
    form = await _parse_upload_form(request)
    upload_id = uuid.uuid4().hex
    dest_dir = jobs.uploads_dir() / upload_id
    dest: Path | None = None
    try:
        candidate = form.get("file")
        if candidate is None or isinstance(candidate, str):
            raise HTTPException(400, "multipart field 'file' is required")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"input{safe_suffix(candidate.filename)}"
        # Chunked + size-capped (the spool file is already on disk — this
        # never touches RAM); to_thread so the copy can't stall the loop.
        size = await asyncio.to_thread(copy_limited, candidate.file, dest,
                                       MAX_UPLOAD_BYTES)
        if size == 0:
            raise UploadInvalid("upload is empty")
        try:
            info = probe_media(dest)
        except FileNotFoundError as e:
            raise UploadInvalid(f"uploaded file unreadable: {e}") from e
        except RuntimeError as e:
            # ffprobe failure or "no audio or video stream" → 400 (bad file).
            raise UploadInvalid(f"not a usable media file: {e}") from e
        if not info.has_video:
            raise UploadInvalid("upload must have a video stream")
        check_duration(float(info.duration))
    except UploadTooLarge as e:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise HTTPException(413, str(e)) from e
    except UploadInvalid as e:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise HTTPException(400, str(e)) from e
    except HTTPException:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    finally:
        await form.close()

    return UPLOADS.add(upload_id, {
        "name": Path(candidate.filename or "upload").name,
        "duration": float(info.duration),
        "width": int(info.width),
        "height": int(info.height),
        "fps": float(info.fps),
        "has_audio": bool(info.has_audio),
        "size": int(size),
    })


# ------------------------------------------------------------------- jobs
@app.post("/jobs", status_code=202)
async def create_job(request: Request) -> dict[str, Any]:
    """Validate a job request and start it in a background thread → 202.

    Validation order follows the contract's status codes: 400 bad
    style/lang/upload payload · 404 unknown upload. Jobs run off the event
    loop (``pipeline.run_job``) because the stages are blocking CPU/ffmpeg.
    """
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(400, f"request body must be JSON: {e}") from e
    if not isinstance(payload, dict):
        raise HTTPException(400, "request body must be a JSON object")

    upload_id = payload.get("upload_id")
    if not isinstance(upload_id, str) or not upload_id.strip():
        raise HTTPException(400, "upload_id is required")
    upload = UPLOADS.get(upload_id)
    if upload is None:
        raise HTTPException(404, f"unknown upload {upload_id!r}")

    lang = payload.get("lang")
    if lang is not None:
        if not isinstance(lang, str):
            raise HTTPException(400, "lang must be a string or null")
        lang = lang.strip() or None      # "" == absent == auto-detect
        if lang is not None and lang not in _LANGS:
            raise HTTPException(
                400, f"unsupported lang {lang!r}; choose from {', '.join(_LANGS)}")

    style = payload.get("style")
    if not isinstance(style, str) or not style.strip():
        raise HTTPException(400, "style is required (see GET /presets)")
    style = style.strip()
    available = list_presets()
    if style not in available:
        raise HTTPException(
            400, f"unknown style {style!r}; available: {', '.join(available)}")

    reuse_of = payload.get("reuse_words_of")
    if reuse_of is not None:
        if not isinstance(reuse_of, str) or not reuse_of.strip():
            raise HTTPException(400, "reuse_words_of must be a job id or null")
        reuse_of = reuse_of.strip()
        if not (jobs.files_dir(reuse_of) / "words.json").is_file():
            raise HTTPException(
                400, f"reuse_words_of {reuse_of!r} has no words.json "
                     "(unknown job or its transcribe stage never finished)")

    options = validate_options(payload.get("options") or {})

    job_id = STORE.create({
        "upload_id": upload_id,
        "upload_path": str(jobs.uploads_dir() / upload_id / dest_name(upload_id)),
        "upload_name": upload["name"],
        "lang": lang,
        "style": style,
        "reuse_words_of": reuse_of,
        "options": options,
    })
    pipeline.run_job(STORE, job_id)
    return {"id": job_id}


def dest_name(upload_id: str) -> str:
    """Stored ``input.<ext>`` filename for an upload (single extension
    rule, shared with the upload route's ``safe_suffix``)."""
    entries = sorted((jobs.uploads_dir() / upload_id).glob("input.*"))
    if not entries:
        raise HTTPException(404, f"unknown upload {upload_id!r} (no stored file)")
    return entries[0].name


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    view = STORE.get(job_id)
    if view is None:
        raise HTTPException(404, f"unknown job {job_id!r}")
    return view


# --------------------------------------------------------------------- SSE
def _sse(event: str, payload: dict[str, Any]) -> str:
    """One SSE frame — contract: event name in ``event:``, JSON in ``data:``
    (single line: ``json.dumps`` never emits raw newlines)."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """Server-Sent Events stream: full stored log replay first (refresh-safe),
    then live events, ``: ping`` heartbeat every ~15 s, close after
    ``done``/``error``.
    """
    if STORE.get(job_id) is None:
        raise HTTPException(404, f"unknown job {job_id!r}")

    async def generator():
        cursor = 0
        while True:
            batch = STORE.events_since(job_id, cursor)
            for name, payload in batch:
                cursor += 1
                yield _sse(name, payload)
                if name in jobs.TERMINAL_EVENTS:
                    return
            if batch:
                continue
            # No buffered events: wait for the pipeline thread (in a worker
            # thread so this loop keeps serving other requests), heartbeat
            # when nothing arrives within the interval.
            has_new = await asyncio.to_thread(STORE.wait_for_events, job_id,
                                              cursor, HEARTBEAT_SECONDS)
            if has_new:
                continue
            view = STORE.get(job_id)
            if view is not None and view["status"] == jobs.STATUS_ERROR:
                # Belt & braces: a terminal status must always close the
                # stream even if its event were somehow missed.
                yield _sse("error", {"error": view["error"] or "job failed"})
                return
            if view is not None and view["status"] == jobs.STATUS_DONE:
                yield _sse("done", {"result": view["result"]})
                return
            yield ": ping\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # nginx: never buffer SSE
        },
    )


# ------------------------------------------------------------------- files
@app.get("/jobs/{job_id}/files/{name}")
def job_file(job_id: str, name: str, dl: int = 0) -> FileResponse:
    """Serve one whitelisted artifact with the contract's Content-Type;
    ``?dl=1`` adds an attachment disposition. 404 for unknown job, unknown
    name, or a file this job never produced."""
    if STORE.get(job_id) is None:
        raise HTTPException(404, f"unknown job {job_id!r}")
    media = ALLOWED_FILES.get(name)
    if media is None:
        raise HTTPException(
            404, f"file {name!r} is not served; allowed: "
                 f"{', '.join(sorted(ALLOWED_FILES))}")
    path = jobs.files_dir(job_id) / name
    if not path.is_file():
        raise HTTPException(404, f"{name} is not available for this job")
    headers = ({"Content-Disposition": f'attachment; filename="{name}"'}
               if dl == 1 else None)
    return FileResponse(path, media_type=media, headers=headers)


if __name__ == "__main__":       # `python app.py` → the contract's default port
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8787)
