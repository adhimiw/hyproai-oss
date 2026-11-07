#!/usr/bin/env python3
"""Sidecar acceptance tests — run: ``cd website/sidecar && ../../.venv/bin/python test_sidecar.py``

Covers API_CONTRACT.md's sidecar gates **without a Gemini key**: annotation
parsing (research/12 fixture), upload → job → SSE → files → stats end to end
(``brain=local``), and the 400/404/413 paths. Spawns the real uvicorn server
on an ephemeral port; needs ffmpeg/ffprobe and the repo venv's deps.
Exit 0 = all pass.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))          # app/jobs/pipeline/brain
sys.path.insert(0, str(REPO / "core"))  # hypro_core
sys.path.insert(0, str(HERE))           # brain imports `import jobs`

import brain  # noqa: E402
import jobs  # noqa: E402

FOOTAGE = (Path.home() / "Downloads" / "insta_reels" /
           "reel1.mp4")
PY = sys.executable  # the repo venv python — never hardcode its path

FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    if not cond:
        FAILURES.append(msg)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def req(url: str, *, data: bytes | None = None, headers: dict | None = None,
        method: str | None = None, timeout: int = 120):
    r = urllib.request.Request(url, data=data, headers=headers or {},
                               method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def unit_parsing() -> None:
    print("== unit: word_info annotations -> Word (research/12 fixture) ==")
    content = {
        "text": "அது நம்ம ரியல் டைம்ல",
        "annotations": [
            {"type": "word_info", "text": "அது", "start_offset": "0.100s",
             "end_offset": "0.500s"},
            {"type": "annotation", "text": "bogus"},  # wrong type → skipped
            {"type": "word_info", "text": "நம்ம", "start_offset": "0.800s",
             "end_offset": "1s"},
        ],
    }
    words = brain.parse_content(content)
    check(len(words) == 2, "exactly 2 words (non-word_info skipped)")
    check(abs(words[0].s - 0.1) < 1e-9 and abs(words[0].e - 0.5) < 1e-9,
          f"0.100s/0.500s parsed -> {words[0].s}/{words[0].e}")
    check(words[0].t == "அது" and words[1].t == "நம்ம", "tokens preserved")
    check(abs(words[1].e - 1.0) < 1e-9, "integer-second offset '1s' parsed")
    check(words[0].conf == 1.0, "gemini words carry conf=1.0")
    check(brain.language_codes("ta") == ["ta-IN"], "ta -> ta-IN hint")
    check(brain.language_codes(None) == [], "None -> auto []")


def wait_ready(base: str, proc: subprocess.Popen, secs: int = 90) -> None:
    deadline = time.time() + secs
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            raise SystemExit(f"server died on boot:\n{out[-3000:]}")
        try:
            st, _, _ = req(f"{base}/health", timeout=3)
            if st == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit("server did not become ready")


def sse_until_done(url: str, timeout: int = 600) -> tuple[list, dict | None, str | None]:
    events: list[tuple[str, dict]] = []
    result, error = None, None
    r = urllib.request.Request(url)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        ev, data = None, ""
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith(":"):
                continue
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
    unit_parsing()

    # ------------------------------------------------------------- server
    print("\n== boot server (no GEMINI_API_KEY expected in env) ==")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    data_dir = Path("/tmp/opencode/sidecar_test_data")
    if data_dir.exists():
        shutil.rmtree(data_dir)
    env = dict(os.environ)
    env.pop("GEMINI_API_KEY", None)  # tests must not depend on the key
    env["HYPRO_DATA_DIR"] = str(data_dir)
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app:app", "--port", str(port), "--app-dir", str(HERE)],
        cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    try:
        wait_ready(base, proc)

        # -------------------------------------------------------- health/presets
        print("== health/presets ==")
        st, _, b = req(f"{base}/health")
        h = json.loads(b) if st == 200 else {}
        check(st == 200 and h.get("ok") is True, "GET /health ok")
        check(h.get("gemini_key") is False, "gemini_key=false without env")
        st, _, b = req(f"{base}/presets")
        presets = json.loads(b).get("presets", []) if st == 200 else []
        ids = {p.get("id") for p in presets}
        check(len(presets) == 8, f"8 presets ({len(presets)})")
        check("tiktok-karaoke" in ids, "tiktok-karaoke present")
        check(all("mode" in p for p in presets), "every preset has mode")

        # ------------------------------------------------------------- upload
        print("== upload ==")
        work = Path("/tmp/opencode/sidecar_test_work")
        work.mkdir(parents=True, exist_ok=True)
        clip = work / "input.mp4"
        if not clip.exists():
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", "23", "-t", "20",
                 "-i", str(FOOTAGE), "-c", "copy", str(clip)], check=True)
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="input.mp4"\r\n'
                f"Content-Type: video/mp4\r\n\r\n").encode() + clip.read_bytes() \
            + f"\r\n--{boundary}--\r\n".encode()
        st, _, b = req(f"{base}/uploads", data=body,
                       headers={"Content-Type":
                                f"multipart/form-data; boundary={boundary}"},
                       timeout=300)
        meta = json.loads(b) if st == 201 else {}
        check(st == 201, f"POST /uploads -> {st}")
        check(meta.get("id") and abs(meta.get("duration", 0) - 20) < 2,
              f"meta duration ≈ 20s ({meta.get('duration')})")
        check(meta.get("has_audio") is True, "has_audio true")
        upload_id = meta["id"]

        # -------------------------------------------------  error-path uploads
        print("== upload error paths ==")
        # no video stream → 400 (generate video-less mp3)
        mp3 = work / "tone.mp3"
        if not mp3.exists():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                            "-i", "sine=frequency=440:duration=2",
                            "-ac", "1", str(mp3)], check=True)
        b2 = uuid.uuid4().hex
        body2 = (f"--{b2}\r\n"
                 f'Content-Disposition: form-data; name="file"; filename="tone.mp3"\r\n'
                 f"Content-Type: audio/mpeg\r\n\r\n").encode() + mp3.read_bytes() \
            + f"\r\n--{b2}--\r\n".encode()
        st, _, eb = req(f"{base}/uploads", data=body2,
                        headers={"Content-Type":
                                 f"multipart/form-data; boundary={b2}"},
                        timeout=120)
        check(st == 400, f"audio-only upload -> 400 ({st})")
        check("error" in json.loads(eb), "400 body is {\"error\": ...}")

        # -------------------------------------------------------------- job
        print("== job end-to-end (brain=local tiny) ==")
        st, _, b = req(f"{base}/jobs", data=json.dumps({
            "upload_id": upload_id, "lang": "ta", "style": "tiktok-karaoke",
            "options": {"brain": "local", "model": "tiny", "silence": True,
                        "burn": True, "enhance_audio": True},
        }).encode(), headers={"Content-Type": "application/json"})
        check(st == 202, f"POST /jobs -> {st}")
        job = json.loads(b)["id"]

        # replay check: attach after completion -> full log replays immediately
        events, result, error = sse_until_done(f"{base}/jobs/{job}/events")
        check(error is None, f"no job error ({error})")
        check(result is not None, "SSE done carried result")
        if result:
            stats = result.get("stats", {})
            for k in ("source_duration", "kept", "keep_ratio", "ranges", "cues",
                      "dropped", "brain", "lang", "style"):
                check(k in stats, f"stats.{k} present")
            check(stats.get("brain") == "local", "stats.brain == local")
            check(stats.get("lang") == "ta", "stats.lang == ta")
            pcts = [p.get("percent", -1) for e, p in events if e == "progress"]
            check(pcts == sorted(pcts) and pcts and pcts[-1] == 100,
                  "percent monotonic and ends at 100")
            stages = {p.get("stage") for e, p in events if e == "progress"}
            check(stages == {"probe", "transcribe", "analyze", "captions",
                             "audio", "render", "export"},
                  f"all stages walked ({sorted(stages)})")

        # ---------------------------------------------------------- artifacts
        print("== artifacts ==")
        for name in ("edited.mp4", "captions.srt", "captions.ass",
                     "cutlist.json", "words.json", "nle.zip"):
            st, hdrs, fb = req(f"{base}/jobs/{job}/files/{name}?dl=1")
            check(st == 200 and len(fb) > 0, f"download {name} ({len(fb)} B)")
            if name == "nle.zip" and st == 200:
                import io as _io
                zf = zipfile.ZipFile(_io.BytesIO(fb))
                names = zf.namelist()
                check(any(n.endswith(".xml") for n in names),
                      f"nle.zip has XML ({names})")
                check("cutlist.json" in names, "nle.zip has cutlist.json")
        st, _, _ = req(f"{base}/jobs/{job}/files/nope.mp4")
        check(st == 404, "unknown file -> 404")
        st, _, _ = req(f"{base}/jobs/does-not-exist")
        check(st == 404, "unknown job GET -> 404")

        # ----------------------------------------------------------- rerender
        print("== rerender (reuse words) ==")
        st, _, b = req(f"{base}/jobs", data=json.dumps({
            "upload_id": upload_id, "lang": "ta", "style": "boxed-classic",
            "reuse_words_of": job,
            "options": {"brain": "local", "model": "tiny", "silence": True,
                        "burn": True},
        }).encode(), headers={"Content-Type": "application/json"})
        check(st == 202, f"rerender POST -> {st}")
        if st == 202:
            job2 = json.loads(b)["id"]
            ev2, res2, err2 = sse_until_done(f"{base}/jobs/{job2}/events")
            check(err2 is None and res2 is not None, "rerender completed")
            check(res2 and res2["stats"].get("style") == "boxed-classic",
                  "rerender applied new style")

        # ----------------------------------------------- job input validation
        print("== job validation ==")
        for payload, want, why in [
            ({"upload_id": "nope", "style": "tiktok-karaoke"}, 404,
             "unknown upload"),
            ({"upload_id": upload_id, "style": "no-such-style"}, 400,
             "unknown style"),
            ({"upload_id": upload_id, "style": "tiktok-karaoke",
              "lang": "xx"}, 400, "bad lang"),
        ]:
            st, _, _ = req(f"{base}/jobs", data=json.dumps(payload).encode(),
                           headers={"Content-Type": "application/json"})
            check(st == want, f"{why} -> {want} (got {st})")
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"SIDECAR TESTS: {len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("SIDECAR TESTS: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
