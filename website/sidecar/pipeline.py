"""Pipeline orchestrator — runs the hypro_core stages for one job.

Mirrors ``hypro_core.cli.cmd_edit`` step for step (API_CONTRACT.md
"Pipeline … mirrors hypro_core.cli cmd_edit"):

    probe → transcribe(brain) → build_cutlist → group_cues + finalize_cues
    (remap_to_record) → compile_ass → enhance_ffmpeg (optional) →
    render_edited (burn after concat) → export_nle → zip nle/ → stats

Two things differ from the CLI: every stage reports progress through the
job store using the contract's percent mapping (probe 0→2 · transcribe 2→45 ·
analyze 45→52 · captions 52→60 · audio 60→72 · render 72→92 · export 92→100,
shared so the web UI stays consistent), and any exception is contained here:
it becomes ``status=error`` plus an ``error`` SSE event whose message names
the failing stage. A job failure must never take the server process down.

Artifacts land in ``$HYPRO_DATA_DIR/jobs/<id>/files/`` — exactly the six
names the ``GET /jobs/{id}/files/{name}`` whitelist serves (``words.json``
is written on every successful transcribe so a failed later stage can still
be re-rendered with ``reuse_words_of``).
"""
from __future__ import annotations

import re
import shutil
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

# Import the core by path (API_CONTRACT.md) and our sibling module by path —
# works when launched as `uvicorn app:app` from any cwd.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_CORE = str(_HERE.parents[1] / "core")          # website/sidecar → <repo>/core
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import jobs                                        # noqa: E402  (sibling store)
import brain                                       # noqa: E402  (gemini adapter)
from hypro_core.analyze import build_cutlist       # noqa: E402
from hypro_core.audio import enhance_ffmpeg        # noqa: E402
from hypro_core.captions import (                  # noqa: E402
    compile_ass, finalize_cues, group_cues, load_preset,
)
from hypro_core.cutlist import Cutlist             # noqa: E402
from hypro_core.export.nle import export_nle, write_srt  # noqa: E402
from hypro_core.media import probe_media           # noqa: E402
from hypro_core.render import render_edited        # noqa: E402
from hypro_core.timing import Transcript, remap_to_record  # noqa: E402

#: Percent mapping per stage (API_CONTRACT.md) — the single source both the
#: sidecar and the web UI use, so progress never jumps around.
STAGE_PERCENT: dict[str, tuple[int, int]] = {
    "probe": (0, 2),
    "transcribe": (2, 45),
    "analyze": (45, 52),
    "captions": (52, 60),
    "audio": (60, 72),
    "render": (72, 92),
    "export": (92, 100),
}

#: Romanization targets (hypro_core.romanize.SUPPORTED, mirrored from cli).
SUPPORTED_LANGS = ("hi", "ta", "te", "ml", "kn", "bn")

#: Rounding for stats (contract example: 30.01 / 28.4 / 0.946).
_DURATION_DP = 2
_RATIO_DP = 3


def safe_name(stem: str) -> str:
    """Filesystem/zip-safe export name for NLE artifacts.

    Keeps ASCII alphanumerics, ``. _ -`` and collapses everything else to
    ``_`` — reel filenames arrive with spaces and unicode, and the export
    folder's ``<name>.xml`` etc. must not carry those into a zip users
    unpack on any platform.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return cleaned or "edit"


def _stats(info: Any, keep: list[tuple[float, float]], cut: Cutlist,
           tr: Transcript, lang: str | None, style: str,
           brain_used: str) -> dict[str, Any]:
    """The contract's stats object — the numbers the results card shows.

    ``dropped`` is a reason → count histogram of the cutlist audit trail
    (``{"silence": 2, "retake": 1}``); ``brain`` reports the brain that
    *actually* produced the word clock (Gemini may have fallen back).
    """
    kept = sum(e - s for s, e in keep)
    hist: dict[str, int] = {}
    for d in cut.dropped:
        reason = str(d.get("reason", "?"))
        hist[reason] = hist.get(reason, 0) + 1
    source = float(info.duration)
    return {
        "source_duration": round(source, _DURATION_DP),
        "kept": round(kept, _DURATION_DP),
        "keep_ratio": round(kept / source, _RATIO_DP) if source > 0 else 0.0,
        "ranges": len(keep),
        "cues": len(cut.captions),
        "dropped": hist,
        "brain": brain_used,
        "lang": lang,
        "style": style,
    }


def _run(store: jobs.JobStore, job_id: str) -> None:
    """Body of the job thread — every stage wrapped by stage-named errors."""
    p = store.params(job_id)
    video = p["upload_path"]
    opts = p["options"]
    lang: str | None = p["lang"]
    style: str = p["style"]
    files = jobs.files_dir(job_id)
    files.mkdir(parents=True, exist_ok=True)
    stage = "probe"
    try:
        # ---------------------------------------------------------- probe
        store.progress(job_id, "probe", STAGE_PERCENT["probe"][0],
                       "probing media")
        info = probe_media(video)
        store.progress(job_id, "probe", STAGE_PERCENT["probe"][1],
                       f"probed {info.duration:.1f}s "
                       f"{info.width}x{info.height}")

        # ----------------------------------------------------- transcribe
        stage = "transcribe"
        brain_name = opts["brain"]
        model = opts["model"]
        reuse_of = p.get("reuse_words_of")
        if reuse_of:
            # Contract: a reuse job still walks every stage; transcribe
            # completes instantly with this message.
            store.progress(job_id, "transcribe", STAGE_PERCENT["transcribe"][0],
                           "reusing cached transcript")
            reuse_path = jobs.files_dir(str(reuse_of)) / "words.json"
            tr = Transcript.load(reuse_path)
            if abs(tr.duration - float(info.duration)) > 1.0:
                raise RuntimeError(
                    f"reused transcript is {tr.duration:.1f}s but the upload is "
                    f"{info.duration:.1f}s — reuse_words_of must reference the "
                    "same source video")
        else:
            store.progress(job_id, "transcribe", STAGE_PERCENT["transcribe"][0],
                           f"transcribing with {brain_name}")
            tr = brain.transcribe(
                video, lang=lang, model=model, brain=brain_name,
                emit=lambda msg: store.log(job_id, "transcribe", msg),
            )
        tr.save(files / "words.json")   # always: rerender + failed-later-stage recovery
        store.progress(job_id, "transcribe", STAGE_PERCENT["transcribe"][1],
                       f"{len(tr.words)} words "
                       f"({tr.language or 'auto-detected'})")

        # Romanization target: the requested language, else the transcript's
        # own language when it is one of our six (same rule as cli.cmd_edit).
        effective_lang = lang or (
            tr.language if tr.language in SUPPORTED_LANGS else None)

        # -------------------------------------------------------- analyze
        stage = "analyze"
        store.progress(job_id, "analyze", STAGE_PERCENT["analyze"][0],
                       "analyzing silence / retakes / fillers")
        cut = build_cutlist(video, tr,
                            silence=bool(opts["silence"]),
                            retake=bool(opts["retake"]),
                            fillers=bool(opts["fillers"]))
        keep = cut.keep_ranges()
        if not keep:
            raise RuntimeError("analysis produced no keep ranges")
        kept = sum(e - s for s, e in keep)
        store.progress(job_id, "analyze", STAGE_PERCENT["analyze"][1],
                       f"keeping {kept:.1f}/{info.duration:.1f}s "
                       f"in {len(keep)} "
                       f"{'range' if len(keep) == 1 else 'ranges'}")

        # ------------------------------------------------------- captions
        stage = "captions"
        store.progress(job_id, "captions", STAGE_PERCENT["captions"][0],
                       f"grouping cues for style {style}")
        preset = load_preset(style)          # validated against /presets
        rec_words = remap_to_record(tr.words, keep)
        cut.captions = finalize_cues(group_cues(rec_words), effective_lang)
        compile_ass(cut.captions, preset,
                    width=info.width or 1920, height=info.height or 1080,
                    out_path=str(files / "captions.ass"))
        write_srt(cut.captions, files / "captions.srt")
        store.progress(job_id, "captions", STAGE_PERCENT["captions"][1],
                       f"{len(cut.captions)} cues ({style})")

        # ---------------------------------------------------------- audio
        stage = "audio"
        store.progress(job_id, "audio", STAGE_PERCENT["audio"][0],
                       "enhancing audio" if opts["enhance_audio"] and info.has_audio
                       else "skipping audio enhancement")
        src_for_render = video
        if opts["enhance_audio"] and info.has_audio:
            src_for_render = enhance_ffmpeg(
                video, str(files / "audio_clean.mp4"))
        store.progress(job_id, "audio", STAGE_PERCENT["audio"][1],
                       "audio stage complete")

        # --------------------------------------------------------- render
        stage = "render"
        store.progress(job_id, "render", STAGE_PERCENT["render"][0],
                       f"rendering {len(keep)} "
                       f"{'cut' if len(keep) == 1 else 'cuts'}"
                       + (" with burned captions" if opts["burn"] else ""))
        ass = str(files / "captions.ass") if opts["burn"] else None
        render_edited(src_for_render, keep, str(files / "edited.mp4"),
                      ass=ass)
        store.progress(job_id, "render", STAGE_PERCENT["render"][1],
                       "rendered edited.mp4")

        # --------------------------------------------------------- export
        stage = "export"
        store.progress(job_id, "export", STAGE_PERCENT["export"][0],
                       "exporting NLE package")
        name = safe_name(Path(video).stem)
        cut.meta.update(name=name, lang=effective_lang, tool="hypro-core",
                        source_duration=float(info.duration),
                        has_audio=bool(info.has_audio))
        cut.save(files / "cutlist.json")
        export_nle(cut, files / "nle", name=name)
        # make_archive(base, root_dir=nle/) → nle.zip whose entries are the
        # folder's contents (no extra top-level dir to strip on download).
        shutil.make_archive(str(files / "nle"), "zip", root_dir=str(files / "nle"))
        stats = _stats(info, keep, cut, tr, effective_lang, style,
                       brain_used=str(tr.engine.get("brain", brain_name)))
        result = {
            "edited": "edited.mp4",
            "srt": "captions.srt",
            "ass": "captions.ass",
            "nle_zip": "nle.zip",
            "cutlist": "cutlist.json",
            "words": "words.json",
            "stats": stats,
        }
        store.progress(job_id, "export", STAGE_PERCENT["export"][1],
                       "export complete")
        store.finish(job_id, result)
    except Exception as e:  # noqa: BLE001 — the thread must never die silently
        traceback.print_exc()
        store.fail(job_id, f"{stage} stage failed: {e}")


def run_job(store: jobs.JobStore, job_id: str) -> None:
    """Spawn :func:`_run` in a daemon thread.

    The pipeline is blocking CPU/ffmpeg work (contract), so it must never
    run on the event loop; daemon=True means a stuck ffmpeg cannot block
    interpreter shutdown — the job simply stays "running" until the process
    ends, which is the honest state.
    """
    threading.Thread(target=_run, args=(store, job_id),
                     name=f"hypro-job-{job_id[:8]}", daemon=True).start()


__all__ = ["STAGE_PERCENT", "run_job", "safe_name", "_stats"]
