#!/usr/bin/env python3
"""Eval harness for the romanization cascade (methodology: research 04 §5).

Metrics per language, on held-out Dakshina sentences (data/eval_<lang>.jsonl):
  1. word accuracy   — token matches the human romanization (any listed spelling)
  2. leak rate       — output still contains native-script codepoints (must be ~0)
  3. source breakdown— % tokens served by glossary/lexicon/neural/scheme/passthrough

Run:  python -m hypro_core.romanize.eval_romanize [--langs hi ta] [--limit 300]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .cascade import Romanizer, has_indic

DATA = Path(__file__).parent / "data"
_LEAK_RE = re.compile(r"[ऀ-ॿঀ-৿஀-௿ఀ-౿ಀ-೿ഀ-ൿ]")


def eval_lang(rom: Romanizer, lang: str, limit: int) -> dict:
    rows = []
    with (DATA / f"eval_{lang}.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            rows.append(json.loads(line))
    rows = rows[:limit]
    gold_path = DATA / f"gold_{lang}.json"
    gold: dict[str, list[str]] = (
        json.loads(gold_path.read_text(encoding="utf-8")) if gold_path.exists() else {}
    )

    correct = total = leaks = 0
    cov = cov_ok = oov = oov_ok = oov_leak = 0
    sources: dict[str, int] = {}
    misses: list[tuple[str, str]] = []
    oov_misses: list[tuple[str, str]] = []

    for row in rows:
        native_toks = [t["native_token"] for t in row["tokens"]]
        human = [t["romanized_token"] for t in row["tokens"]]
        got_src = [rom.romanize_token(w, lang) for w in native_toks]
        for (g, src), h, n in zip(got_src, human, native_toks):
            sources[src] = sources.get(src, 0) + 1
            total += 1
            # accept any attested spelling (Dakshina methodology), not just the
            # one human variant captured in this sentence
            ok = g.lower() == h.lower() or g.lower() in {
                s.lower() for s in gold.get(n, [])
            }
            if ok:
                correct += 1
            elif len(misses) < 5:
                misses.append((n, f"{h} → {g}"))
            if _LEAK_RE.search(g):
                leaks += 1
            # OOV slice: Indic token NOT in lexicon → exercises L3 (neural/scheme)
            if has_indic(n) and rom.lexicon_lookup(n, lang) is None:
                oov += 1
                if ok:
                    oov_ok += 1
                elif len(oov_misses) < 5:
                    oov_misses.append((n, f"{h} → {g}"))
                if _LEAK_RE.search(g):
                    oov_leak += 1
            else:
                cov += 1
                if ok:
                    cov_ok += 1

    return {
        "lang": lang,
        "words": total,
        "accuracy": correct / total if total else 0.0,
        "leak_rate": leaks / total if total else 0.0,
        "coverage": cov / total if total else 0.0,
        "lexicon_acc": cov_ok / cov if cov else 0.0,
        "oov_words": oov,
        "oov_acc": oov_ok / oov if oov else 0.0,
        "oov_leak": oov_leak / oov if oov else 0.0,
        "sources": sources,
        "sample_misses": misses,
        "oov_misses": oov_misses,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", nargs="+", default=["hi", "ta", "te", "ml", "kn", "bn"])
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--no-neural", action="store_true")
    args = ap.parse_args(argv)

    print(f"{'lang':<5} {'words':>6} {'acc':>7} {'leak':>7} {'cov':>6} {'lexAcc':>7} {'oovAcc':>7}  sources")
    print("-" * 96)
    worst = 0.0
    for lang in args.langs:
        if not (DATA / f"eval_{lang}.jsonl").exists():
            print(f"{lang:<5} (no eval file — run build_lexicon.py)")
            continue
        rom = Romanizer(lang=lang, neural=not args.no_neural)
        r = eval_lang(rom, lang, args.limit)
        worst = max(worst, r["leak_rate"])
        src = " ".join(f"{k}:{v}" for k, v in sorted(r["sources"].items()))
        print(
            f"{r['lang']:<5} {r['words']:>6} {r['accuracy']:>6.1%} {r['leak_rate']:>6.1%}"
            f" {r['coverage']:>5.1%} {r['lexicon_acc']:>6.1%} {r['oov_acc']:>6.1%}  {src}"
        )
        for n, detail in r["sample_misses"][:2]:
            print(f"      miss: {n}  ({detail})")
        if r["oov_words"]:
            print(f"      OOV slice: {r['oov_words']} words, leak {r['oov_leak']:.1%}")
            for n, detail in r["oov_misses"][:2]:
                print(f"      oov-miss: {n}  ({detail})")
    ok = worst <= 0.01
    print(f"LEAK GATE (≤1%): {'PASS' if ok else 'FAIL'} (worst {worst:.2%})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
