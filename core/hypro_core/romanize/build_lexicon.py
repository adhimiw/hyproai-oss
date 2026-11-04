#!/usr/bin/env python3
"""Build L2 attested-spelling lexicons from the Dakshina HF mirror (CC-BY-SA-4.0).

Input : data/raw/<lang>.parquet  (native/romanized sentences + lexicon_mapping + token_alignment)
Output: data/lexicon_<lang>.json  {native_word: [best_roman, ...]}  (ranked by attestation)
        data/eval_<lang>.jsonl    held-out sentence pairs for the eval harness

Attribution: Dakshina dataset © Google Research, LREC 2020, CC-BY-SA-4.0.
Derived lexicons remain CC-BY-SA-4.0 (share-alike). The MIT-licensed code in this
repo does not apply CC terms to itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
RAW = HERE / "data" / "raw"
OUT = HERE / "data"
LANGS = ["hi", "ta", "te", "ml", "kn", "bn"]
EVAL_PER_LANG = 1000
MAX_SPELLINGS = 5


def build(lang: str) -> tuple[int, int]:
    df = pd.read_parquet(RAW / f"{lang}.parquet")

    # 0. deterministic eval split FIRST — held-out rows must not feed the lexicon
    aligned_idx = []
    for i, row in enumerate(df.itertuples(index=False)):
        try:
            toks = [
                (t["native_token"], t["romanized_token"])
                for t in row.token_alignment
                if t.get("native_token") and t.get("romanized_token")
            ]
        except (TypeError, AttributeError):
            toks = []
        if toks:
            aligned_idx.append((i, toks))
    step = max(1, len(aligned_idx) // EVAL_PER_LANG)
    eval_positions = sorted(set(range(0, len(aligned_idx), step)))[:EVAL_PER_LANG]
    eval_rows = [aligned_idx[p] for p in eval_positions]
    eval_keys = {i for i, _ in eval_rows}
    train_df = df.drop(index=list(eval_keys)) if eval_keys else df

    # native word -> ordered spellings (lexicon_mapping preserves original
    # Dakshina attestation order: first spelling = most attested)
    lex: dict[str, list[str]] = {}
    align_votes: dict[str, dict[str, int]] = {}

    for row in train_df.itertuples(index=False):
        # 1. lexicon mappings (frequency-ranked by construction)
        try:
            mapping = json.loads(row.lexicon_mapping) if isinstance(row.lexicon_mapping, str) else {}
        except (json.JSONDecodeError, TypeError):
            mapping = {}
        for native, romans in (mapping or {}).items():
            bucket = lex.setdefault(native, [])
            for r in romans:
                if isinstance(r, str) and r and r not in bucket:
                    bucket.append(r)

        # 2. human sentence-level choice as a vote-breaker
        try:
            for tok in row.token_alignment:
                n, r = tok.get("native_token"), tok.get("romanized_token")
                if n and r:
                    align_votes.setdefault(n, {})
                    align_votes[n][r] = align_votes[n].get(r, 0) + 1
        except (TypeError, AttributeError):
            pass

    # merge: attestation order first, then human-choice frequency for words
    # the lexicon didn't rank (or to surface the sentence-preferred spelling)
    final: dict[str, list[str]] = {}
    for native, spellings in lex.items():
        votes = align_votes.get(native, {})
        # keep attestation order but promote a human-voted spelling to front
        # if it got >= 2 votes and isn't already first
        if votes:
            best_vote = max(votes, key=votes.get)
            if votes[best_vote] >= 2 and best_vote in spellings:
                spellings.remove(best_vote)
                spellings.insert(0, best_vote)
            elif best_vote not in spellings:
                spellings.insert(0, best_vote)
        final[native] = spellings[:MAX_SPELLINGS]

    # words only seen in sentence alignment (no lexicon entry): add with vote order
    for native, votes in align_votes.items():
        if native not in final:
            ordered = sorted(votes, key=votes.get, reverse=True)
            final[native] = ordered[:MAX_SPELLINGS]

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"lexicon_{lang}.json").write_text(
        json.dumps(final, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    # gold set for SCORING ONLY: every attested spelling of a word across the
    # FULL dataset (incl. eval rows) — Dakshina's methodology accepts any
    # attested spelling. Never used for ranking/lookup at runtime.
    gold: dict[str, set[str]] = {}
    for row in df.itertuples(index=False):
        try:
            mapping = json.loads(row.lexicon_mapping) if isinstance(row.lexicon_mapping, str) else {}
        except (json.JSONDecodeError, TypeError):
            mapping = {}
        for native, romans in (mapping or {}).items():
            gold.setdefault(native, set()).update(r for r in romans if isinstance(r, str) and r)
        try:
            for tok in row.token_alignment:
                n, r = tok.get("native_token"), tok.get("romanized_token")
                if n and r:
                    gold.setdefault(n, set()).add(r)
        except (TypeError, AttributeError):
            pass
    (OUT / f"gold_{lang}.json").write_text(
        json.dumps({k: sorted(v) for k, v in gold.items()}, ensure_ascii=False,
                   separators=(",", ":")),
        encoding="utf-8",
    )

    # eval split: held-out rows (excluded from lexicon above)
    with (OUT / f"eval_{lang}.jsonl").open("w", encoding="utf-8") as fh:
        for i, toks in eval_rows:
            row = df.iloc[i]
            fh.write(json.dumps(
                {"native": row["native"], "romanized": row["romanized"],
                 "tokens": [{"native_token": n, "romanized_token": r} for n, r in toks]},
                ensure_ascii=False) + "\n")

    return len(final), len(eval_rows)


def main() -> int:
    if not RAW.exists():
        print("data/raw missing — download parquets first", file=sys.stderr)
        return 1
    for lang in LANGS:
        if not (RAW / f"{lang}.parquet").exists():
            print(f"{lang}: no parquet, skipped")
            continue
        n_lex, n_eval = build(lang)
        print(f"{lang}: {n_lex:,} lexicon words, {n_eval} eval sentences")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
