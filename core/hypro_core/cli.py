"""hypro — command-line entry for the hypro-core pipeline.

    hypro edit video.mp4 --lang ta --style tiktok-karaoke --export-nle
    hypro transcribe video.mp4 --lang ta -o words.json
    hypro romanize "வணக்கம் நண்பர்களே" --lang ta
    hypro presets list
    hypro export cutlist.json --out ./nle --name my_edit

The `edit` command is the whole pipeline in one shot:

    probe → transcribe (word clock) → analyze (silence/retake/filler → Cutlist)
          → captions (group + romanize → Thanglish) → audio enhance
          → render (cuts + burned ASS) → export folder (XML/OTIO/SRT/JSX/EDL)

Stage modules are imported lazily so single-stage commands keep working even
when an optional stage (e.g. neural ASR extras) is unavailable.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SUPPORTED_LANGS = ("hi", "ta", "te", "ml", "kn", "bn")


class _Step:
    """Stage timer — one line per pipeline stage, so long runs stay legible."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.t0 = 0.0

    def __enter__(self) -> "_Step":
        self.t0 = time.time()
        print(f"→ {self.label}…", flush=True)
        return self

    def __exit__(self, *exc) -> None:
        if exc[0] is None:
            print(f"  ✓ {self.label} ({time.time() - self.t0:.1f}s)", flush=True)
        else:
            print(f"  ✗ {self.label} failed", flush=True)


def _die(msg: str, code: int = 1) -> "None":
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


# ------------------------------------------------------------------ edit
def cmd_edit(args: argparse.Namespace) -> int:
    from .media import probe_media

    video = str(Path(args.input).expanduser().resolve())
    info = probe_media(video)
    out = Path(args.out or f"{Path(video).stem}_hypro").expanduser()
    out.mkdir(parents=True, exist_ok=True)
    name = Path(video).stem

    print(f"source : {video}")
    print(f"meta   : {info.duration:.1f}s  {info.width}x{info.height}  "
          f"{info.fps:.2f}fps  audio={'yes' if info.has_audio else 'no'}")

    # ---------------------------------------------------------- 1. word clock
    if args.words_json:
        from .timing import Transcript

        with _Step(f"load words ({args.words_json})"):
            tr = Transcript.load(args.words_json)
    else:
        from .transcribe import transcribe

        with _Step(f"transcribe (model={args.model})"):
            tr = transcribe(video, lang=args.lang, model=args.model)
            tr.save(out / "words.json")
        print(f"  lang={tr.language}  words={len(tr.words)}  "
              f"engine={tr.engine.get('asr')}")

    lang = args.lang or (tr.language if tr.language in SUPPORTED_LANGS else None)
    if args.lang and args.lang not in SUPPORTED_LANGS:
        _die(f"unsupported lang {args.lang!r}; choose from {SUPPORTED_LANGS}")
    if lang is None and not args.no_captions:
        print("  note: no romanization target (ASR language not in "
              f"{SUPPORTED_LANGS}); captions stay as transcribed")

    # ---------------------------------------------------------- 2. cutlist
    from .analyze import build_cutlist

    with _Step("analyze (silence/retake/filler)"):
        cut = build_cutlist(
            video, tr,
            silence=not args.no_silence,
            retake=args.retake,
            fillers=args.fillers,
        )
    keep = cut.keep_ranges()
    if not keep:
        _die("analysis dropped everything — check the source or loosen thresholds")
    kept = sum(e - s for s, e in keep)
    hist: dict[str, int] = {}
    for d in cut.dropped:
        hist[d.get("reason", "?")] = hist.get(d.get("reason", "?"), 0) + 1
    print(f"  keep {kept:.1f}/{info.duration:.1f}s ({kept / info.duration:.0%}) "
          f"in {len(keep)} ranges | dropped: {hist or 'none'}")
    if args.dry_run:
        print("dry-run: stopping after analyze")
        cut.save(out / "cutlist.json")
        return 0

    # ---------------------------------------------------------- 3. captions
    ass_path: Path | None = None
    if not args.no_captions:
        from .captions import compile_ass, finalize_cues, group_cues, load_preset
        from .timing import remap_to_record

        with _Step("captions (group → romanize → ASS)"):
            rec_words = remap_to_record(tr.words, keep)
            cues = finalize_cues(group_cues(rec_words), lang)
            cut.captions = cues
            try:
                preset = load_preset(args.style)
            except FileNotFoundError:
                from .captions import list_presets

                avail = list_presets()
                _die(f"unknown preset {args.style!r}; available: {avail}")
            ass_path = out / "captions.ass"
            compile_ass(cues, preset, width=info.width or 1920,
                        height=info.height or 1080, out_path=str(ass_path))
            print(f"  {len(cues)} cues, style={args.style}, "
                  f"lang={lang or 'as-transcribed'}")

    # ---------------------------------------------------------- 4. audio
    src_for_render = video
    if not args.no_audio and info.has_audio:
        from .audio import enhance_ffmpeg

        with _Step("audio enhance (denoise → compress → loudnorm)"):
            enhanced = out / "audio_clean.mp4"
            src_for_render = enhance_ffmpeg(video, str(enhanced))

    # ---------------------------------------------------------- 5. render
    from .render import render_edited

    final = out / f"{name}_edited.mp4"
    burn = ass_path if (ass_path and not args.no_burn) else None
    with _Step(f"render ({len(keep)} cuts"
               + (", captions burned" if burn else "") + ")"):
        render_edited(src_for_render, keep, str(final),
                      ass=str(burn) if burn else None, crf=args.crf)
    print(f"  → {final}")

    # ---------------------------------------------------------- 6. export
    cut.meta.update(name=name, lang=lang, tool="hypro-core",
                    source_duration=info.duration,
                    has_audio=bool(info.has_audio))
    if not args.no_nle:
        from .export import export_nle

        with _Step("NLE export (XML/OTIO/SRT/JSX/EDL)"):
            files = export_nle(cut, out / "nle", name=name)
        print(f"  → {out / 'nle'} ({len(files)} files)")
    elif cut.captions:
        from .export.nle import write_srt

        write_srt(cut.captions, out / f"{name}.srt")

    cut.save(out / "cutlist.json")
    print(f"\ndone: {final}")
    return 0


# ------------------------------------------------------------- transcribe
def cmd_transcribe(args: argparse.Namespace) -> int:
    from .media import probe_media
    from .transcribe import transcribe

    video = str(Path(args.input).expanduser().resolve())
    probe_media(video)  # fail fast with a readable error
    with _Step(f"transcribe (model={args.model})"):
        tr = transcribe(video, lang=args.lang, model=args.model)
    out = Path(args.output or "words.json")
    tr.save(out)
    print(f"lang={tr.language} words={len(tr.words)} duration={tr.duration:.1f}s "
          f"→ {out}")
    return 0


# -------------------------------------------------------------- romanize
def cmd_romanize(args: argparse.Namespace) -> int:
    from .romanize import SUPPORTED, Romanizer

    if args.lang not in SUPPORTED:
        _die(f"unsupported lang {args.lang!r}; choose from {SUPPORTED}")
    rom = Romanizer(lang=args.lang, glossary=args.glossary)
    for line in (args.text or sys.stdin.read()).splitlines():
        if line.strip():
            print(rom.romanize(line))
    return 0


# --------------------------------------------------------------- presets
def cmd_presets(_args: argparse.Namespace) -> int:
    from .captions import list_presets

    for p in list_presets():
        print(p)
    return 0


# ---------------------------------------------------------------- export
def cmd_export(args: argparse.Namespace) -> int:
    from .cutlist import Cutlist
    from .export import export_nle

    cut = Cutlist.load(args.cutlist)
    files = export_nle(cut, args.out, name=args.name)
    for k, v in files.items():
        print(f"{k:12} {v}")
    return 0


# ------------------------------------------------------------------ main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="hypro",
        description="Open-source AI video editor for Indian-language video "
                    "(Thanglish captions, silence/retake removal, NLE export).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("edit", help="run the full pipeline on a video")
    e.add_argument("input", help="input video/audio file")
    e.add_argument("--lang", help="language + romanization target "
                                  f"(one of {', '.join(SUPPORTED_LANGS)})")
    e.add_argument("--model", default="small",
                   help="whisper model: tiny/base/small/medium/large-v3 "
                        "(default: small)")
    e.add_argument("--style", default="tiktok-karaoke",
                   help="caption preset name (see: hypro presets)")
    e.add_argument("--no-captions", action="store_true")
    e.add_argument("--no-burn", action="store_true",
                   help="keep captions as sidecar only, don't burn into video")
    e.add_argument("--no-silence", action="store_true")
    e.add_argument("--retake", action="store_true",
                   help="enable retake removal (conservative)")
    e.add_argument("--fillers", action="store_true",
                   help="enable filler-word removal")
    e.add_argument("--no-audio", action="store_true",
                   help="skip audio enhancement")
    e.add_argument("--no-nle", action="store_true",
                   help="skip the NLE export folder")
    e.add_argument("--words-json", help="reuse an existing words.json "
                                        "(skips transcription)")
    e.add_argument("--out", help="output directory (default: <input>_hypro/)")
    e.add_argument("--crf", type=int, default=18, help="x264 quality (default 18)")
    e.add_argument("--dry-run", action="store_true",
                   help="stop after the analyze stage")
    e.set_defaults(func=cmd_edit)

    t = sub.add_parser("transcribe", help="produce words.json only")
    t.add_argument("input")
    t.add_argument("--lang")
    t.add_argument("--model", default="small")
    t.add_argument("-o", "--output", help="output path (default words.json)")
    t.set_defaults(func=cmd_transcribe)

    r = sub.add_parser("romanize", help="romanize text on stdin/args (Thanglish)")
    r.add_argument("text", nargs="?", help="text to romanize (reads stdin if omitted)")
    r.add_argument("--lang", required=True,
                   help=f"one of {', '.join(SUPPORTED_LANGS)}")
    r.add_argument("--glossary", help="JSON file of {native: latin} overrides")
    r.set_defaults(func=cmd_romanize)

    p = sub.add_parser("presets", help="list caption presets")
    p.add_argument("list", nargs="?", default="list")
    p.set_defaults(func=cmd_presets)

    x = sub.add_parser("export", help="re-export NLE folder from cutlist.json")
    x.add_argument("cutlist", help="path to cutlist.json")
    x.add_argument("--out", required=True)
    x.add_argument("--name", default="edit")
    x.set_defaults(func=cmd_export)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        _die(f"file not found: {e}")
    except RuntimeError as e:
        _die(str(e))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
