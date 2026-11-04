"""Thanglish/Hinglish/Telugish/… romanization cascade (the "Text Magic" layer).

Deterministic 4-layer cascade, per research doc 04-romanization.md §6:

    L0  token gate      — zero Indic codepoints → pass through verbatim
                         (code-switch protection: English stays English)
    L1  user glossary   — persistent overrides (names, slang, brand spellings)
    L2  lexicon         — Dakshina attested colloquial spellings (83–92% coverage)
    L3  rules + neural  — language rule pack, then IndicXlit CT2 for OOV,
                         with beam re-ranking against the L2 spelling list
    L4  (optional, not here) guarded LLM polish — lives in the export path

Layers 0–3 are local, deterministic, µs–ms per token. No network at runtime
(lexicons ship in-package; the CT2 model downloads once, 44 MB, then caches).

Licensing: lexicons derived from Dakshina © Google Research (CC-BY-SA-4.0),
attribution in NOTICE. Code MIT.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from .rules import apply_rules

DATA = Path(__file__).parent / "data"
SUPPORTED = ("hi", "ta", "te", "ml", "kn", "bn")

# Unicode script blocks → language (for auto-detecting a token's script)
_SCRIPT_RANGES: tuple[tuple[str, int, int], ...] = (
    ("hi", 0x0900, 0x097F),   # Devanagari
    ("bn", 0x0980, 0x09FF),   # Bengali
    ("ta", 0x0B80, 0x0BFF),   # Tamil
    ("te", 0x0C00, 0x0C7F),   # Telugu
    ("kn", 0x0C80, 0x0CFF),   # Kannada
    ("ml", 0x0D00, 0x0D7F),   # Malayalam
)

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def _script_of(ch: str) -> str | None:
    cp = ord(ch)
    for lang, lo, hi in _SCRIPT_RANGES:
        if lo <= cp <= hi:
            return lang
    return None


def has_indic(text: str) -> bool:
    """True if text contains any codepoint from our six Indic blocks."""
    return any(_script_of(ch) for ch in text)


def detect_lang(text: str, default: str | None = None) -> str | None:
    """Majority script in text → language code (or default)."""
    votes: dict[str, int] = {}
    for ch in text:
        s = _script_of(ch)
        if s:
            votes[s] = votes.get(s, 0) + 1
    if not votes:
        return default
    return max(votes, key=votes.get)


class Romanizer:
    """Cached, reusable romanizer. Load once per process.

    Parameters
    ----------
    lang : default language for tokens whose script is unambiguous
           (per-token script still wins, so hi+ta code-switching works).
    glossary : optional path to a JSON override file {native: latin}.
    neural : enable the IndicXlit CT2 OOV fallback (downloads 44 MB once).
    """

    def __init__(
        self,
        lang: str = "hi",
        glossary: str | Path | None = None,
        neural: bool = True,
    ) -> None:
        if lang not in SUPPORTED:
            raise ValueError(f"lang must be one of {SUPPORTED}, got {lang!r}")
        self.lang = lang
        self._lexicon: dict[str, dict[str, list[str]]] = {}
        self._glossary: dict[str, str] = {}
        self._neural_enabled = neural
        self._translator = None
        self._neural_failed = False
        for l in SUPPORTED:
            p = DATA / f"lexicon_{l}.json"
            if p.exists():
                self._lexicon[l] = json.loads(p.read_text(encoding="utf-8"))
        if glossary:
            self._glossary.update(json.loads(Path(glossary).read_text(encoding="utf-8")))
        builtin = DATA / "glossary.json"
        if builtin.exists():
            # built-in seed glossary never overrides a user file
            for k, v in json.loads(builtin.read_text(encoding="utf-8")).items():
                self._glossary.setdefault(k, v)

    # ------------------------------------------------------------------ L1
    def set_override(self, native: str, latin: str) -> None:
        """Learn a spelling at runtime (one edit fixes every future video)."""
        self._glossary[native] = latin

    # ------------------------------------------------------------------ L2
    def lexicon_lookup(self, word: str, lang: str) -> str | None:
        entry = self._lexicon.get(lang, {}).get(word)
        if entry:
            return entry[0]  # attestation-ranked best spelling
        return None

    def _spelling_set(self, lang: str) -> set[str]:
        """All attested Roman spellings for one language (beam re-ranking)."""
        key = f"_spell_{lang}"
        cached = getattr(self, key, None)
        if cached is None:
            cached = {r for forms in self._lexicon.get(lang, {}).values() for r in forms}
            setattr(self, key, cached)
        return cached

    # ------------------------------------------------------------------ L3-neural
    def _get_translator(self):
        if self._translator is None and not self._neural_failed:
            try:
                import ctranslate2
                from huggingface_hub import snapshot_download

                path = snapshot_download(
                    "Singla0009/all-indic-transliteration",
                    allow_patterns="indicxlit_indic_en_ct2_fp32/*",
                )
                import os

                self._translator = ctranslate2.Translator(
                    os.path.join(path, "indicxlit_indic_en_ct2_fp32"), device="cpu"
                )
            except Exception:
                # offline / no ctranslate2 → cascade degrades to rules-only
                self._neural_failed = True
                self._translator = None
        return self._translator

    def _neural(self, word: str, lang: str) -> str:
        tr = self._get_translator()
        if tr is None:
            return ""  # caller falls back to scheme/rules
        try:
            res = tr.translate_batch(
                [[f"__{lang}__"] + list(word)],
                beam_size=4,
                num_hypotheses=4,
            )[0]
        except Exception:
            return ""
        # re-rank beam: attested spelling wins; else best CT2 score
        cands = ["".join(h) for h in res.hypotheses]
        known = self._spelling_set(lang)
        for c in cands:
            fixed = apply_rules(c, lang)
            if fixed in known or c in known:
                return fixed
        return apply_rules(cands[0], lang) if cands else ""

    # ------------------------------------------------------------------ scheme fallback
    @staticmethod
    def _scheme(word: str, lang: str) -> str:
        """indic-transliteration pivot (used only when CT2 is unavailable)."""
        try:
            from indic_transliteration import sanscript

            script = {
                "hi": sanscript.DEVANAGARI,
                "bn": sanscript.BENGALI,
                "ta": sanscript.TAMIL,
                "te": sanscript.TELUGU,
                "kn": sanscript.KANNADA,
                "ml": sanscript.MALAYALAM,
            }[lang]
            return sanscript.transliterate(word, script, sanscript.ITRANS)
        except Exception:
            return word

    # ------------------------------------------------------------------ token
    def romanize_token(self, token: str, lang: str | None = None) -> tuple[str, str]:
        """Romanize ONE token → (result, source).

        source ∈ {passthrough, glossary, lexicon, neural, scheme, rules-only}
        so callers can audit quality per word.
        """
        token = unicodedata.normalize("NFC", token)
        # L0: no letters at all (digits, emoji, punctuation) → untouched
        if not any(ch.isalpha() for ch in token):
            return token, "passthrough"
        tscript = detect_lang(token)
        if tscript is None:
            return token, "passthrough"  # L0: Latin/numbers/emoji untouched
        lang = tscript or lang or self.lang

        if token in self._glossary:            # L1
            return self._glossary[token], "glossary"

        hit = self.lexicon_lookup(token, lang)  # L2
        if hit is not None:
            return hit, "lexicon"

        neural = self._neural(token, lang)      # L3
        if neural:
            return apply_rules(neural, lang), "neural"

        scheme = self._scheme(token, lang)      # L3 last resort
        if has_indic(scheme):                   # scheme leaked native glyphs
            return token, "rules-only"          # give up loudly, keep original
        return apply_rules(scheme, lang), "scheme"

    # ------------------------------------------------------------------ text
    def romanize(self, text: str) -> str:
        """Romanize a full line/cue. Whitespace and punctuation preserved.

        Splits each whitespace-delimited chunk into same-script RUNS (not by
        \\w — Python's \\w excludes Indic combining marks like Tamil pulli,
        which would shred words into letter fragments). Latin runs stay
        byte-identical inside mixed-script chunks (code-switch protection).
        """
        out: list[str] = []
        for chunk in re.split(r"(\s+)", text):
            if not chunk or chunk.isspace() or not has_indic(chunk):
                out.append(chunk)
                continue
            piece: list[str] = []
            cur_script = _script_of(chunk[0])
            cur = chunk[0]
            for ch in chunk[1:]:
                s = _script_of(ch)
                if s == cur_script:
                    cur += ch
                else:
                    piece.append(self._run(cur, cur_script))
                    cur_script, cur = s, ch
            piece.append(self._run(cur, cur_script))
            out.append("".join(piece))
        return "".join(out)

    def _run(self, run: str, script: str | None) -> str:
        # a run with no letters (bare marks/digits/punct) stays verbatim
        if script is None or not any(ch.isalpha() for ch in run):
            return run
        return self.romanize_token(run, script)[0]

    def romanize_words(self, words: list[str]) -> list[tuple[str, str]]:
        """Word-timed path: list in, list of (roman, source) out — no re-splitting,
        so caption word indices stay aligned with ASR timestamps."""
        return [self.romanize_token(w) for w in words]


@lru_cache(maxsize=4)
def get_romanizer(lang: str = "hi") -> Romanizer:
    """Process-wide shared instance (lexicons are ~2 MB/lang; load once)."""
    return Romanizer(lang=lang)


def romanize(text: str, lang: str = "hi") -> str:
    """One-shot convenience API."""
    return get_romanizer(lang).romanize(text)
