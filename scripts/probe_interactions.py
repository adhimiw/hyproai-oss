#!/usr/bin/env python3
"""Probe the Gemini Interactions API: response shape for plain + structured output."""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY = next(
    line.split("=", 1)[1].strip()
    for line in (ROOT / ".env").read_text().splitlines()
    if line.startswith("GEMINI_API_KEY=")
)


def call(body: dict) -> dict:
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        data=json.dumps(body).encode(),
        headers={
            "x-goog-api-key": KEY,
            "Content-Type": "application/json",
            "Api-Revision": "2026-05-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:800]}


def main() -> int:
    # 1. plain text
    r1 = call({"model": "gemini-3.5-flash", "input": "Reply with exactly: OK", "store": False})
    print("=== PLAIN ===")
    if "_http_error" in r1:
        print(r1)
        return 1
    print("top keys:", sorted(r1.keys()))
    print("output_text:", repr(r1.get("output_text"))[:200])
    steps = r1.get("steps") or []
    print("steps:", len(steps))
    if steps:
        last = steps[-1]
        print("last step keys:", sorted(last.keys()))
        print(json.dumps(last, ensure_ascii=False)[:800])

    # 2. structured
    schema = {
        "type": "object",
        "properties": {
            "clips": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "src_in": {"type": "number"},
                        "src_out": {"type": "number"},
                        "rec_in": {"type": "number"},
                    },
                    "required": ["src_in", "src_out", "rec_in"],
                },
            }
        },
        "required": ["clips"],
    }
    r2 = call(
        {
            "model": "gemini-3.5-flash",
            "input": (
                "Cutlist: keep 2.0-5.5s and 10.25-13.75s of source. "
                "Output clips with rec_in contiguous from 0."
            ),
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            "store": False,
        }
    )
    print("=== STRUCTURED ===")
    if "_http_error" in r2:
        print(r2)
        return 1
    print("top keys:", sorted(r2.keys()))
    print("output_text:", repr(r2.get("output_text"))[:600])
    return 0


if __name__ == "__main__":
    sys.exit(main())
