"""L3 post-rules: colloquial Hinglish/Thanglish/etc. normalization.

These run AFTER the lexicon (L2) and neural fallback (L3-neural) so they only
touch derived tokens — never Latin pass-through tokens (the cascade guarantees
that). Each rule is (regex, replacement) applied in order; keep lists small and
evidence-based: the lexicon already covers 83–92% of tokens naturally.
"""
from __future__ import annotations

import re

# Hindi → chat-natural Hinglish (IndicXlit training style leaks: naheen/pataa/kii)
HI_RULES: list[tuple[str, str]] = [
    (r"\bnaheen\b", "nahi"),
    (r"\bnaheeN\b", "nahi"),
    (r"\bpataa\b", "pata"),
    (r"\bkii\b", "ki"),
    (r"\bkahaan\b", "kahan"),
    (r"\bkahoN\b", "kaho"),
    (r"\bvahaaN\b", "wahan"),
    (r"\byahaaN\b", "yahan"),
    (r"\bhU~N\b", "hu"),
    (r"\bhU\.N\b", "hu"),
    (r"\bhayE\b", "hai"),
    (r"\bhaye\b", "hai"),
    (r"\bhE\b", "hai"),
    (r"\bhoON\b", "hoon"),
    (r"\bThIka\b", "theek"),
    (r"\bachChA\b", "achha"),
    (r"\bachcha\b", "achha"),
    (r"\bbaad mein\b", "baad mein"),
    (r"\bposte\b", "pata"),  # rare CT2 artifact
    (r"\back\b", "ek"),      # the documented garbage token for एक
    (r"\bAck\b", "ek"),
    (r"\bik\b", "ek"),
    (r"\bvoe\b", "wo"),
    (r"\bhuye\b", "hue"),
    (r"\bhUA\b", "hua"),
    (r"\bsaathhee\b", "sathi"),
    (r"\bliee\b", "liye"),
    (r"\bdeshaan\b", "deshan"),
]

# Tamil → Thanglish conventions (dict-form CT2 output → spoken form)
TA_RULES: list[tuple[str, str]] = [
    (r"\bneengkal\b", "neenga"),
    (r"\bneengaL\b", "neenga"),
    (r"\beppadi\b", "epdi"),
    (r"\birukkireerkal\b", "irukkeenga"),
    (r"\birukkirathu\b", "irukku"),
    (r"\bavarthalir\b", "avaru"),
    (r"\benakku\b", "enaku"),
    (r"\bennum\b", "enna"),
    (r"\bpinnala\b", "pinnala"),
    (r"\bivarthalir\b", "ivaru"),
    (r"\bsari\b", "sari"),
    (r"\bnandri\b", "nandri"),
]

# Telugu → Telugish
TE_RULES: list[tuple[str, str]] = [
    (r"\bunnaru\b", "unnaru"),
    (r"\blera\b", "ledu"),
    (r"\bledu\b", "ledu"),
    (r"\bchestunnanu\b", "chestunna"),
    (r"\bunnanu\b", "unnanu"),
]

# Malayalam → Malayalish (light touch: ML CT2 output is already close)
ML_RULES: list[tuple[str, str]] = [
    (r"\billa\b", "illa"),
    (r"\bkanam\b", "kano"),
]

# Kannada → Kanglish
KN_RULES: list[tuple[str, str]] = [
    (r"\bhegiddira\b", "hegiddira"),
    (r"\bnenapide\b", "nenapide"),
    (r"\bagide\b", "agide"),
]

# Bengali → Benglish
BN_RULES: list[tuple[str, str]] = [
    (r"\bkemon\b", "kemon"),
    (r"\bachen\b", "achen"),
    (r"\bbhalo\b", "bhalo"),
    (r"\bsotti\b", "sotti"),
    (r"\bky\b", "ki"),
]

# generic cleanup applied to ALL derived tokens (never Latin pass-through)
GENERIC_RULES: list[tuple[str, str]] = [
    (r"è", "e"),        # sanscript artifacts (fallback path)
    (r"ē", "e"),
    (r"ō", "o"),
    (r"ā", "a"),
    (r"ī", "i"),
    (r"ū", "u"),
    (r"~N", "n"),       # sanscript anusvara/tail artifacts
    (r"~n", "n"),
    (r"(\w)~", r"\1"),  # trailing tilde
    (r"([bcdfghjklmnpqrstvwxyz])\1{2,}", r"\1\1"),  # triple consonant → double
]

LANG_RULES: dict[str, list[tuple[str, str]]] = {
    "hi": HI_RULES,
    "ta": TA_RULES,
    "te": TE_RULES,
    "ml": ML_RULES,
    "kn": KN_RULES,
    "bn": BN_RULES,
}


def apply_rules(word: str, lang: str) -> str:
    """Apply language pack then generic cleanup. Deterministic, ordered."""
    for pat, rep in LANG_RULES.get(lang, []):
        word = re.sub(pat, rep, word, flags=re.IGNORECASE)
    for pat, rep in GENERIC_RULES:
        word = re.sub(pat, rep, word)
    return word
