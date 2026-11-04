"""In-memory job + upload store with a per-job SSE event log.

API_CONTRACT.md v1 mandates a single process with an in-memory job dict
("one process, in-memory job dict (v1 — Redis later)"), so this module is
the whole state layer: uploads, jobs, and the ordered event log that
``GET /jobs/{id}/events`` replays before following live.

Why one lock for everything: jobs are mutated by background pipeline
threads (CPU/ffmpeg work) while HTTP handlers and SSE generators read them
from the asyncio loop. A single ``threading.Condition`` guarding the dicts
makes "replay stored events, then wait for live ones" race-free — a writer
either has appended the event (replay sees it) or is about to (the waiter's
predicate sees it after ``notify_all``), never both-didn't.

The event log is a plain ``list[(event, payload)]`` per contract; a reader
tracks its own cursor position, and list indices are stable under append,
so no sequence numbers are needed.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

#: Every stage message, fallback notice and failure is mirrored here so
#: ``docker logs -f`` / the process stdout is a usable backend log — the SSE
#: event log alone is invisible to operators (and gone when the job leaves
#: memory). Logged outside the condition so a slow handler can't stall jobs.
_log = logging.getLogger("hypro.job")

#: ``<repo>`` — sidecar/ sits two levels below the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Job statuses (API_CONTRACT.md ``GET /jobs/{id}``).
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

#: SSE event names that terminate a stream (contract: "then close").
TERMINAL_EVENTS = frozenset({"done", "error"})


def data_root() -> Path:
    """Data root: ``HYPRO_DATA_DIR`` env else ``<repo>/data`` (contract).

    Everything the sidecar writes lives under here: ``uploads/<id>/input.<ext>``
    and ``jobs/<id>/files/*``.
    """
    env = os.environ.get("HYPRO_DATA_DIR", "").strip()
    return Path(env) if env else REPO_ROOT / "data"


def uploads_dir() -> Path:
    return data_root() / "uploads"


def job_dir(job_id: str) -> Path:
    return data_root() / "jobs" / job_id


def files_dir(job_id: str) -> Path:
    """Artifacts directory of a job — what ``GET /jobs/{id}/files`` serves."""
    return job_dir(job_id) / "files"


class UploadStore:
    """Upload metadata for this process (the file on disk is the source of truth).

    Why memory only: v1 is a single process (contract); an upload is just a
    validated path plus the ffprobe facts the UI wants echoed back. On a
    restart the upload rows are gone but the files remain on disk — jobs
    validate against this store, so stale files simply become unreachable
    rather than silently resumable, which is the safe failure mode.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, dict[str, Any]] = {}

    def add(self, upload_id: str, meta: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._items[upload_id] = dict(meta, id=upload_id)
            return dict(self._items[upload_id])

    def get(self, upload_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(upload_id)
            return dict(item) if item is not None else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class JobStore:
    """Thread-safe job records + per-job SSE event logs.

    Public view of a job is exactly the contract shape::

        {id, status, stage, percent, message, error, result}

    ``params`` (the validated POST /jobs payload plus resolved paths) is
    internal — the pipeline reads it once at thread start.
    """

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._jobs: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------- writes
    def create(self, params: dict[str, Any]) -> str:
        """Register a queued job and return its id (uuid4 hex)."""
        job_id = uuid.uuid4().hex
        with self._cv:
            self._jobs[job_id] = {
                "id": job_id,
                "status": STATUS_QUEUED,
                "stage": "probe",      # first stage of the contract's stage list
                "percent": 0,
                "message": "queued",
                "error": None,
                "result": None,
                "params": dict(params),
                "events": [],
            }
        return job_id

    def emit(self, job_id: str, event: str, payload: dict[str, Any]) -> None:
        """Append ``(event, payload)`` to the job's log and wake SSE readers."""
        with self._cv:
            job = self._locked(job_id)
            job["events"].append((event, payload))
            self._cv.notify_all()

    def progress(self, job_id: str, stage: str, percent: int,
                 message: str) -> None:
        """Record a ``progress`` event and update the job's live fields.

        Also flips ``queued`` → ``running``: the first progress event of a
        job always arrives when its thread enters the first stage.
        """
        payload = {"stage": stage, "percent": int(percent), "message": message}
        with self._cv:
            job = self._locked(job_id)
            if job["status"] == STATUS_QUEUED:
                job["status"] = STATUS_RUNNING
            job.update(stage=stage, percent=int(percent), message=message)
            job["events"].append(("progress", payload))
            self._cv.notify_all()
        _log.info("job %s %-10s %3d%% %s",
                  job_id[:8], stage, int(percent), message)

    def log(self, job_id: str, stage: str, message: str) -> None:
        """Record a non-fatal ``log`` notice (contract: brain fallback etc.)."""
        payload = {"stage": stage, "message": message}
        with self._cv:
            job = self._locked(job_id)
            job["events"].append(("log", payload))
            self._cv.notify_all()
        _log.warning("job %s %-10s note: %s", job_id[:8], stage, message)

    def finish(self, job_id: str, result: dict[str, Any]) -> None:
        """Mark the job done, pin percent at 100 and emit the ``done`` event."""
        with self._cv:
            job = self._locked(job_id)
            job.update(status=STATUS_DONE, stage="done", percent=100,
                       message="done", result=dict(result))
            job["events"].append(("done", {"result": dict(result)}))
            self._cv.notify_all()
        stats = result.get("stats") or {}
        _log.info("job %s done       kept=%.1fs/%ss cues=%s brain=%s",
                  job_id[:8], float(stats.get("kept") or 0),
                  stats.get("source_duration"), stats.get("cues"),
                  stats.get("brain"))

    def fail(self, job_id: str, message: str) -> None:
        """Mark the job errored and emit the ``error`` event (then SSE closes)."""
        with self._cv:
            job = self._locked(job_id)
            job.update(status=STATUS_ERROR, error=message,
                       message=message)
            job["events"].append(("error", {"error": message}))
            self._cv.notify_all()
        _log.error("job %s FAILED: %s", job_id[:8], message)

    # -------------------------------------------------------------- reads
    def get(self, job_id: str) -> dict[str, Any] | None:
        """Contract-shaped public view, or ``None`` for an unknown id."""
        with self._cv:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return {
                "id": job["id"],
                "status": job["status"],
                "stage": job["stage"],
                "percent": job["percent"],
                "message": job["message"],
                "error": job["error"],
                "result": job["result"],
            }

    def params(self, job_id: str) -> dict[str, Any]:
        """Copy of the job's creation params (pipeline input). KeyError if unknown."""
        with self._cv:
            return dict(self._locked(job_id)["params"])

    def events_since(self, job_id: str, after: int) -> list[tuple[str, dict[str, Any]]]:
        """Events with index >= ``after`` (index == list position, stable)."""
        with self._cv:
            events = self._locked(job_id)["events"]
            return list(events[after:])

    def wait_for_events(self, job_id: str, after: int, timeout: float) -> bool:
        """Block up to ``timeout`` for an event past index ``after``.

        Returns True when at least one new event is readable. Used by the SSE
        generator through ``asyncio.to_thread`` so heartbeats keep flowing
        while the pipeline thread is busy in ffmpeg.
        """
        with self._cv:
            self._locked(job_id)
            return self._cv.wait_for(
                lambda: len(self._jobs[job_id]["events"]) > after,
                timeout=timeout,
            )

    def __len__(self) -> int:
        with self._cv:
            return len(self._jobs)

    # ----------------------------------------------------------- internals
    def _locked(self, job_id: str) -> dict[str, Any]:
        """Job record assuming ``self._cv`` is held; KeyError with a readable msg."""
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"unknown job {job_id!r}")
        return job
