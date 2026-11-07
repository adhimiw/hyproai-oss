#!/usr/bin/env python3
"""Website integration test — drives the real stack per website/API_CONTRACT.md.

Prereqs: sidecar running (default http://localhost:8787); optionally the Next.js
dev/prod server (default http://localhost:3000) to verify the /api/sidecar
rewrite incl. SSE passthrough.

    # terminal 1
    cd website/sidecar && ../../.venv/bin/python -m uvicorn app:app --port 8787
    # terminal 2
    cd website/web && npm run dev          # optional but recommended
    # terminal 3
    .venv/bin/python website/integration_test.py [--web http://localhost:3000]
        [--brain local|gemini] [--keep]

Exit 0 = all pass. `--brain gemini` needs GEMINI_API_KEY in the sidecar env.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIDECAR = "http://localhost:8787"
FOOTAGE = (Path.home() / "Downloads" / "insta_reels" /
           "reel1.mp4")

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        FAILURES.append(msg)


def req(url: str, *, data: bytes | None = None, headers: dict | None = None,
        method: str | None = None, timeout: int = 60):
    r = urllib.request.Request(url, data=data, headers=headers or {},
                               method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def json_req(url: str, payload: dict, *, method: str = "POST"):
    st, _, b = req(url, data=json.dumps(payload).encode(),
                   headers={"Content-Type": "application/json"},
                   method=method)
    return st, (json.loads(b) if b else None)


def upload(path: Path, base: str = SIDECAR) -> dict:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{path.name}"\r\n'
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    st, _, b = req(
        f"{base}/uploads", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=300,
    )
    if st != 201:
        raise SystemExit(f"upload failed {st}: {b[:400]!r}")
    return json.loads(b)


def sse_until_done(url: str, *, timeout: int = 900) -> tuple[list, dict | None, str | None]:
    """Consume an SSE stream; returns (events, final_result, error)."""
    events: list[tuple[str, dict]] = []
    r = urllib.request.Request(url)
    result = None
    error = None
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        ev, data = None, ""
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\n").rstrip("\r")
            if line.startswith(":"):
                continue  # heartbeat
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
            elif line == "" and ev:
                payload = json.loads(data) if data else {}
                events.append((ev, payload))
                if ev == "done":
                    result = payload.get("result")
                if ev == "error":
                    error = payload.get("error")
                    return events, None, error
                ev, data = None, ""
                if result is not None:
                    return events, result, None
    return events, result, error


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--web", default="http://localhost:3000",
                    help="Next.js origin (empty string to skip)")
    ap.add_argument("--brain", default="local", choices=["local", "gemini"])
    ap.add_argument("--model", default="tiny")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    # ---------------------------------------------------------- health
    print("== health/presets ==")
    st, _, b = req(f"{SIDECAR}/health")
    health = json.loads(b) if st == 200 else {}
    check(st == 200 and health.get("ok") is True, "GET /health ok")
    print(f"     gemini_key={health.get('gemini_key')}")
    st, _, b = req(f"{SIDECAR}/presets")
    presets = json.loads(b).get("presets", []) if st == 200 else []
    ids = {p.get("id") for p in presets}
    check(st == 200 and len(presets) >= 6, f"GET /presets >= 6 ({len(presets)})")
    check("tiktok-karaoke" in ids, "preset tiktok-karaoke present")

    # ---------------------------------------------------------- upload
    print("\n== upload ==")
    work = Path("/tmp/opencode/website_integration")
    work.mkdir(parents=True, exist_ok=True)
    clip = work / "input.mp4"
    if not clip.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", "23", "-t",
             str(args.seconds), "-i", str(FOOTAGE), "-c", "copy", str(clip)],
            check=True,
        )
    meta = upload(clip)
    print(f"     upload id={meta['id']} {meta['duration']:.1f}s "
          f"{meta['width']}x{meta['height']}")
    check(meta["duration"] > 0 and meta["has_audio"], "upload meta sane")
    st, _, _ = req(f"{SIDECAR}/uploads/does-not-exist", method="POST")
    check(st in (404, 405, 422), f"bad upload rejected ({st})")

    # ---------------------------------------------------------- job
    print(f"\n== job (brain={args.brain}) ==")
    st, body = json_req(f"{SIDECAR}/jobs", {
        "upload_id": meta["id"], "lang": "ta", "style": "tiktok-karaoke",
        "options": {"brain": args.brain, "model": args.model,
                    "silence": True, "burn": True},
    })
    check(st == 202 and body and body.get("id"), f"POST /jobs -> {st}")
    job = body["id"]

    t0 = time.time()
    events, result, error = sse_until_done(f"{SIDECAR}/jobs/{job}/events")
    dt = time.time() - t0
    stages = [p.get("stage") for e, p in events if e == "progress"]
    print(f"     {len(events)} events in {dt:.0f}s; stages seen: {stages}")
    check(error is None, f"job completed without error ({error})")
    check(result is not None, "SSE done carried result")
    # Brain honesty. `log` is emitted ONLY on the gemini -> local fallback
    # path (brain.py transcribe()), so a clean Gemini run must have no log
    # event, and a fallback must never be silent: the two must move together.
    # (A fallback also flips stats.brain to "local", caught at line ~230.)
    if result is not None and args.brain == "gemini":
        brain_used = (result.get("stats") or {}).get("brain")
        fell_back = any(e == "log" for e, _ in events)
        check((brain_used == "gemini") != fell_back,
              f"brain reporting honest (used={brain_used}, "
              f"fallback logged={fell_back})")
    if result is None:
        report()

    # monotonic percent + stage walk
    pcts = [p.get("percent", -1) for e, p in events if e == "progress"]
    check(pcts == sorted(pcts), "percent monotonic")
    check(all(s in {"probe", "transcribe", "analyze", "captions", "audio",
                    "render", "export", "done"} for s in stages),
          "stages within contract enum")

    # ---------------------------------------------------------- artifacts
    print("\n== artifacts ==")
    st, _, b = req(f"{SIDECAR}/jobs/{job}")
    got = json.loads(b)
    check(got["status"] == "done" and got["stage"] == "done",
          "GET /jobs/{id} status=done")
    check(got.get("result"), "GET /jobs/{id} result present")

    files_dir = work / "files"
    files_dir.mkdir(exist_ok=True)
    for name in ("edited.mp4", "captions.srt", "captions.ass",
                 "cutlist.json", "words.json", "nle.zip"):
        st, hdrs, b = req(f"{SIDECAR}/jobs/{job}/files/{name}?dl=1")
        check(st == 200 and len(b) > 0, f"download {name} ({len(b)} bytes)")
        if st == 200:
            (files_dir / name).write_bytes(b)
            if name == "nle.zip":
                zf = zipfile.ZipFile(files_dir / name)
                names = zf.namelist()
                check(any(n.endswith(".xml") for n in names),
                      f"nle.zip contains XML ({names})")
    st, _, _ = req(f"{SIDECAR}/jobs/{job}/files/nope.zip")
    check(st == 404, "unknown file -> 404")

    # content assertions
    cut = json.loads((files_dir / "cutlist.json").read_text())
    kept = sum(c["src_out"] - c["src_in"] for c in cut["tracks"]["video"])
    edited_dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(files_dir / "edited.mp4")],
        capture_output=True, text=True, check=True).stdout.strip())
    check(abs(edited_dur - kept) <= max(0.5, 0.03 * kept),
          f"edited {edited_dur:.1f}s ≈ kept {kept:.1f}s")
    srt = (files_dir / "captions.srt").read_text()
    check("-->" in srt and len(srt) > 20, "srt has cues")
    leak = any(any("஀" <= ch <= "௿" or "ऀ" <= ch <= "ॿ" or
                   "ఀ" <= ch <= "౿" or "ಀ" <= ch <= "೿" or
                   "ഀ" <= ch <= "ൿ" or "ঀ" <= ch <= "৿"
                   for ch in cue["text"]) for cue in cut.get("captions", []))
    check(not leak, "no native-script leak in captions")
    stats = result["stats"]
    for k in ("source_duration", "kept", "keep_ratio", "ranges", "cues",
              "dropped", "brain", "lang", "style"):
        check(k in stats, f"stats.{k} present")
    check(stats["brain"] == args.brain, f"stats.brain == {args.brain}")

    # ---------------------------------------------------------- rerender
    print("\n== rerender (reuse words) ==")
    st, body2 = json_req(f"{SIDECAR}/jobs", {
        "upload_id": meta["id"], "lang": "ta", "style": "neon-glow",
        "reuse_words_of": job,
        "options": {"brain": args.brain, "model": args.model,
                    "silence": True, "burn": True},
    })
    check(st == 202, f"rerender POST /jobs -> {st}")
    if st == 202:
        t1 = time.time()
        _, result2, error2 = sse_until_done(
            f"{SIDECAR}/jobs/{body2['id']}/events")
        check(error2 is None and result2 is not None, "rerender done")
        if result2:
            st, _, srt2 = req(
                f"{SIDECAR}/jobs/{body2['id']}/files/captions.srt")
            check(st == 200 and b"-->" in srt2, "rerender srt ok")
            check(result2["stats"]["style"] == "neon-glow",
                  "rerender style applied")
            print(f"     rerender took {time.time() - t1:.0f}s "
                  f"(transcribe skipped)")

    # ---------------------------------------------------------- web rewrite
    if args.web:
        print(f"\n== web rewrite ({args.web}) ==")
        try:
            st, _, b = req(f"{args.web}/", timeout=10)
            html = b.decode("utf-8", "replace")
            check(st == 200 and ("<html" in html.lower()),
                  "GET / returns HTML")
            st, _, b = req(f"{args.web}/api/sidecar/health", timeout=10)
            check(st == 200 and json.loads(b).get("ok") is True,
                  "rewrite /api/sidecar/health works")

            # Large upload through the proxy — regression gate for the 10 MB
            # rewrite truncation that used to surface as a sidecar 500
            # (ClientDisconnect on /uploads). Uploads now go through the
            # streaming route handler app/api/sidecar/uploads/route.ts.
            big = FOOTAGE
            if big.exists() and big.stat().st_size > 10 * 1024 * 1024:
                try:
                    big_meta = upload(big, base=f"{args.web}/api/sidecar")
                    check(big_meta.get("size", 0) == big.stat().st_size,
                          f"upload {big.stat().st_size // (1024*1024)} MB "
                          "through proxy (full body received)")
                except SystemExit as e:
                    check(False, f"large upload through proxy ({e})")
            else:
                print(f"     (skipped large-upload gate: {big} absent)")
            # SSE through the rewrite (the risky one) — fresh tiny job
            st, body3 = json_req(f"{args.web}/api/sidecar/jobs", {
                "upload_id": meta["id"], "lang": "ta",
                "style": "boxed-classic",
                "options": {"brain": "local", "model": args.model},
            })
            if st == 202:
                events3, res3, err3 = sse_until_done(
                    f"{args.web}/api/sidecar/jobs/{body3['id']}/events",
                    timeout=600)
                check(err3 is None and res3 is not None,
                      f"SSE through Next rewrite works ({len(events3)} events)")
            else:
                check(False, f"job via rewrite accepted ({st})")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            check(False, f"web reachable ({e})")

    return report(keep=args.keep)


def report(keep: bool = False) -> int:
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"INTEGRATION: {len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("INTEGRATION: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
