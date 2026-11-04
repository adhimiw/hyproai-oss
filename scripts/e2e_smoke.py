#!/usr/bin/env python3
"""End-to-end smoke test for the hypro pipeline.

Runs the real `hypro edit` CLI on a short cut of real footage and asserts the
contract of every output. Kept as a plain script (no pytest) so it runs in the
bare venv:

    .venv/bin/python scripts/e2e_smoke.py [--keep] [--seconds 30]

Stages covered: probe → transcribe → analyze → captions(romanize+ASS) →
audio enhance → render → NLE export.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "core"
PY = REPO / ".venv" / "bin" / "python"
FOOTAGE = (Path.home() / "Downloads" / "insta_reels" /
           "Janani ｜ Vocal⧸Veena Tutor_Dd9FDSsxDgH.mp4")

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {msg}")
    if not cond:
        FAILURES.append(msg)


def ffprobe_dur(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--lang", default=None,
                    help="force lang (default: let ASR detect)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the temp workspace for inspection")
    args = ap.parse_args()

    if not FOOTAGE.exists():
        print(f"footage missing: {FOOTAGE}", file=sys.stderr)
        return 2
    if not PY.exists():
        print(f"venv missing: {PY}", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="hypro_e2e_", dir="/tmp/opencode"))
    print(f"workspace: {work}")

    # 1. cut a short clip from real footage (skip the first 5s intro)
    clip = work / "input.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", "5", "-t", str(args.seconds),
         "-i", str(FOOTAGE), "-c", "copy", str(clip)],
        check=True,
    )
    src_dur = ffprobe_dur(clip)
    print(f"source clip: {src_dur:.2f}s")

    # 2. run the full CLI
    cmd = [str(PY), "-m", "hypro_core.cli", "edit", str(clip),
           "--out", str(work / "out"), "--style", "tiktok-karaoke"]
    if args.lang:
        cmd += ["--lang", args.lang]
    print("running:", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=CORE, capture_output=True, text=True, timeout=1800)
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr[-3000:], file=sys.stderr)
        check(False, f"CLI exit code 0 (got {proc.returncode})")
        return report(work, args.keep)

    out = work / "out"
    edited = next(out.glob("*_edited.mp4"), None)

    # 3. assertions
    print("\nassertions:")
    check(edited is not None and edited.exists(), "edited mp4 exists")
    check((out / "words.json").exists(), "words.json exists")
    check((out / "cutlist.json").exists(), "cutlist.json exists")
    check((out / "captions.ass").exists(), "captions.ass exists")
    nle = out / "nle"
    check(nle.exists(), "nle/ folder exists")
    if nle.exists():
        xmls = list(nle.glob("*.xml"))
        srts = list(nle.glob("*.srt"))
        check(len(xmls) == 1, "one .xml (FCP7)")
        check(len(srts) == 1, "one .srt")
        check((nle / "cutlist.json").exists(), "nle/cutlist.json exists")
        check(list((nle / "tracks").glob("*.edl")) if (nle / "tracks").exists()
              else False, "per-track EDLs exist")
        if xmls:
            import xml.etree.ElementTree as ET
            try:
                ET.parse(xmls[0])
                check(True, "xml well-formed")
            except ET.ParseError as e:
                check(False, f"xml well-formed ({e})")

    if edited and edited.exists():
        edur = ffprobe_dur(edited)
        cut = json.loads((out / "cutlist.json").read_text())
        kept = sum(c["src_out"] - c["src_in"] for c in cut["tracks"]["video"])
        check(abs(edur - kept) <= max(0.5, 0.03 * kept),
              f"edited duration {edur:.1f}s ≈ kept {kept:.1f}s")
        check(edur < src_dur, f"edited ({edur:.1f}s) < source ({src_dur:.1f}s) — cuts happened")
        check(len(cut.get("captions", [])) > 0, "captions present in cutlist")
        # Thanglish leak check: no native-script codepoints in caption text
        leak = any(any("஀" <= ch <= "௿" or "ऀ" <= ch <= "ॿ" or
                       "ఀ" <= ch <= "౿" or "ಀ" <= ch <= "೿" or
                       "ഀ" <= ch <= "ൿ" or "ঀ" <= ch <= "৿"
                       for ch in cue["text"]) for cue in cut.get("captions", []))
        check(not leak, "no native-script leak in burned caption text")
        # karaoke sum check on the ASS
        ass = (out / "captions.ass").read_text(encoding="utf-8")
        check(ass.count("[Events]") == 1 and "Dialogue:" in ass,
              "ASS has Dialogue events")

    return report(work, args.keep)


def report(work: Path, keep: bool) -> int:
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"E2E: {len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print(f"  - {f}")
        print(f"workspace kept: {work}")
        return 1
    print("E2E: ALL PASS")
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"workspace: {work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
