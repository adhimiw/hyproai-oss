#!/usr/bin/env python3
"""Is Tamil actually supported by gemini-3.5-transcribe?

`ta-IN` is missing from the supported-languages hint table (docs §supported-
languages lists hi/te/kn/ml/bn but no ta), yet the same doc says language
detection is automatic and the table is for *hints*. Two calls on real Tamil
speech answer it:

    A  auto-detect (language_codes omitted) + word timestamps
       -> does it recognize Tamil at all, and do word annotations parse?
    B  explicit hint language_codes=["ta-IN"] (unlisted code)
       -> accepted and accurate, or rejected?

Pass/fail is objective: the transcript must contain Tamil-script codepoints
(U+0B80..U+0BFF) in the right proportion, and word offsets must parse.

Usage:  .venv/bin/python scripts/probe_transcribe_tamil.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY = next(
    line.split("=", 1)[1].strip()
    for line in (ROOT / ".env").read_text().splitlines()
    if line.startswith("GEMINI_API_KEY=")
)
FOOTAGE = (Path.home() / "Downloads" / "insta_reels" /
           "Vigneshwaran A_DePMR2xyGLL.mp4")
# slice [85s, 93.4s): verified clean Tamil speech (local ASR: 20 words,
# tamil-script ratio 1.00) — regions 0-6.4 are dead air and 41-54 makes the
# local small model hallucinate, so neither is usable as ground truth.
SS, SECONDS = 85.0, 8.4
WORK = Path("/tmp/opencode/tamil_probe")

TAMIL = re.compile(r"[\u0b80-\u0bff]")


def http(url: str, *, data: bytes | None = None, headers: dict | None = None,
         method: str | None = None, timeout: int = 180) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def upload_file(path: Path) -> str:
    """Files API resumable upload (raw REST, no SDK). Returns the file URI."""
    size = path.stat().st_size
    mime = "audio/mp3" if path.suffix == ".mp3" else "audio/wav"
    status, hdrs, body = http(
        "https://generativelanguage.googleapis.com/upload/v1beta/files",
        data=json.dumps({"file": {"display_name": path.name}}).encode(),
        headers={
            "x-goog-api-key": KEY,
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
        },
        method="POST",
    )
    if status != 200:
        raise SystemExit(f"upload start failed: {status} {body[:400]!r}")
    upload_url = hdrs.get("X-Goog-Upload-URL") or hdrs.get("x-goog-upload-url")
    if not upload_url:
        raise SystemExit(f"no upload URL in headers: {hdrs}")

    status, _, body = http(
        upload_url,
        data=path.read_bytes(),
        headers={
            "x-goog-api-key": KEY,
            "Content-Type": "application/octet-stream",
            "X-Goog-Upload-Command": "upload, finalize",
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
        },
        method="POST",
    )
    if status != 200:
        raise SystemExit(f"upload finalize failed: {status} {body[:400]!r}")
    uri = json.loads(body)["file"]["uri"]
    # files can land in PROCESSING; poll until ACTIVE (usually instant)
    name = uri.rsplit("/", 1)[-1]
    for _ in range(30):
        st, _, b = http(f"https://generativelanguage.googleapis.com/"
                        f"v1beta/files/{name}", headers={"x-goog-api-key": KEY})
        state = json.loads(b).get("state", "ACTIVE") if st == 200 else "ACTIVE"
        if state == "ACTIVE":
            return uri
        if state == "FAILED":
            raise SystemExit(f"file processing FAILED: {b[:400]!r}")
        time.sleep(2)
    return uri


def transcribe(uri: str, *, language_codes: list[str] | None,
               words: bool) -> dict:
    """One interactions call. Returns the model_output content item."""
    mode: dict = {"type": "verbatim"}
    if words:
        mode["timestamp_granularities"] = ["word"]
    body: dict = {
        "model": "models/gemini-3.5-transcribe",
        "input": [{"type": "audio", "uri": uri, "mime_type": "audio/mp3"}],
        "generation_config": {"transcription_config": {"mode": mode}},
        "store": False,
    }
    if language_codes is not None:
        body["generation_config"]["transcription_config"]["language_codes"] = \
            language_codes
    last = ""
    for attempt in range(6):
        st, _, b = http(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            data=json.dumps(body).encode(),
            headers={"x-goog-api-key": KEY, "Content-Type": "application/json",
                     "Api-Revision": "2026-05-20"},
            method="POST", timeout=300,
        )
        if st == 200:
            resp = json.loads(b)
            for step in resp.get("steps", []):
                if step.get("type") == "model_output":
                    for c in step.get("content", []):
                        if c.get("type") == "text":
                            return c
            return {"_error": "no model_output text", "_raw": resp}
        detail = b.decode(errors="replace")[:400]
        last = f"HTTP {st}: {detail}"
        if st in (429, 500, 502, 503, 504):
            m = re.search(r"retry in (\d+)s", detail)
            time.sleep(float(m.group(1)) if m else min(3 * 2 ** attempt, 60))
            continue
        break
    raise RuntimeError(last)


def tamil_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if TAMIL.match(c)) / len(letters)


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    clip = WORK / "tamil_8s.mp3"
    if not clip.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(SS), "-t", str(SECONDS),
             "-i", str(FOOTAGE), "-vn", "-ac", "1", "-ar", "16000",
             "-b:a", "64k", str(clip)],
            check=True,
        )
    print(f"clip: {clip} ({clip.stat().st_size} bytes)")

    uri = upload_file(clip)
    print(f"uploaded: {uri}\n")

    fails: list[str] = []

    # ---- A: auto-detect + word timestamps -------------------------------
    print("=== A: auto-detect (no language hint) + word timestamps ===")
    try:
        content = transcribe(uri, language_codes=None, words=True)
    except RuntimeError as e:
        content = {"_error": str(e)}
    text = content.get("text", "") or ""
    print(f"text: {text[:300]}")
    anns = [a for a in content.get("annotations", [])
            if a.get("type") == "word_info"]
    print(f"words: {len(anns)}  first: "
          f"{[(a.get('text'), a.get('start_offset'), a.get('end_offset')) for a in anns[:5]]}")
    r = tamil_ratio(text)
    print(f"tamil-script letter ratio: {r:.2f}")
    if content.get("_error"):
        fails.append(f"A: call failed: {content['_error']}")
    elif r < 0.5:
        fails.append(f"A: not Tamil (ratio {r:.2f}) — auto-detect missed it")
    if not anns:
        fails.append("A: no word_info annotations")
    else:
        try:
            t0 = float(anns[0]["start_offset"].rstrip("s"))
            t1 = float(anns[-1]["end_offset"].rstrip("s"))
            if not (0 <= t0 < t1 <= SECONDS + 5):
                fails.append(f"A: offsets out of range {t0}..{t1}")
            print(f"offset span: {t0:.3f}s -> {t1:.3f}s (clip {SECONDS}s) OK")
        except Exception as e:
            fails.append(f"A: offset parse: {e}")

    # ---- B: explicit ta-IN hint (unlisted in the docs table) ------------
    print("\n=== B: explicit language_codes=['ta-IN'] hint ===")
    try:
        content_b = transcribe(uri, language_codes=["ta-IN"], words=False)
    except RuntimeError as e:
        content_b = {"_error": str(e)}
    text_b = content_b.get("text", "") or ""
    print(f"text: {text_b[:300]}")
    rb = tamil_ratio(text_b)
    print(f"tamil-script letter ratio: {rb:.2f}")
    if content_b.get("_error"):
        print("hint rejected — auto-detect (A) is the path to use")
        fails.append(f"B: hint call failed: {content_b['_error']}")
    elif rb < 0.5:
        fails.append(f"B: hint produced non-Tamil (ratio {rb:.2f})")

    # ---- local reference for eyeballing ---------------------------------
    print("\n=== local faster-whisper reference (same clip) ===")
    sys.path.insert(0, str(ROOT / "core"))
    from hypro_core.transcribe import transcribe as local_tr
    tr = local_tr(str(clip), lang="ta", device="cpu")
    print(f"local: {tr.text()[:300]}")

    print("\n=== verdict ===")
    if fails:
        for f in fails:
            print(f"FAIL - {f}")
        return 1
    print("PASS - Tamil works: auto-detect recognizes it, word timestamps "
          "parse, ta-IN hint accepted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
