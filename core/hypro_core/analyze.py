"""Stage A2 — cut engine: silence / retake / filler → keep ranges → Cutlist.

Three analyzers share one word clock (research/05 §6.0: "one shared
word-accurate timeline"), each returning SOURCE-timeline keep ranges plus
audit entries; :func:`build_cutlist` composes them as **union of drops →
complement → merge** (i.e. the keep ranges intersect) and packs the result
into a :class:`~hypro_core.cutlist.Cutlist`.

Conservative by construction (PIPELINE mandate): a region is only dropped
when it is *clearly* dead air, a *clear* repeat, or an unambiguous filler.
Every threshold below is the researched default, not a guess:

* silence — research/05 §6.1: gaps < ``merge_gap`` (0.80 s) are **never**
  cut; surviving keeps get asymmetric breathing room ``margin`` = 0.15 s
  before / 0.35 s after each kept span (auto-editor ``--margin 0.15s,0.35s``;
  speakers inhale before the next word, so pads are asymmetric — research/05
  §1.6); isolated speech islands shorter than ``floor`` (0.30 s) are
  coughs/false starts (timkulbaev) and drop too. Because the detector *is*
  the word clock, cutting also requires that the clock speaks for at least
  half the timeline — otherwise there is no evidence that the rest is dead
  air rather than content (music beds, instrumental reels, a botched ASR
  pass), and deleting on absent evidence is how a silence remover shreds a
  video (see ``_MIN_SPEECH_EXTENT``).
* retakes — research/05 §6.2 v1 (Stages A+C1+D only, no embeddings): split
  into sentence groups on gaps >= 1.0 s, compare *consecutive* groups with
  token Jaccard >= 0.55 AND word-count ratio >= 0.5, keep the better-scored
  take, and refuse to act when the transcript itself is unreliable
  (mean word conf < 0.45, Stage B3) or when 3+ groups match in a row
  (deliberate refrain, Stage B1).
* fillers — research/05 §6.3: drop only fillers >= 0.30 s, and skip any
  filler within 80 ms of a neighbouring word boundary (Descript's
  "avoid harsh cuts": a tight filler would clip a real word).

Ambiguous open-class words ("like", "so", "well", "wait", "okay" …) are
deliberately **not** in the lexicon: dropping them would violate the
conservative mandate (research/05 §3.2 rule 7 sends that long tail to an
LLM/review pass, which is out of v1 scope).
"""
from __future__ import annotations

import statistics
import string

from .cutlist import Cutlist
from .timing import Transcript, Word

# research/05 §6.2 Stage B3 — below this mean word confidence the transcript
# is unreliable (accent/crosstalk) and no retake may be auto-cut.
_RETAKE_CONF_FLOOR = 0.45
# research/05 §6.2 Stage B2 — >2.5x duration disparity means "two different
# sentences that merely start alike", not two takes of one line.
_RETAKE_LEN_DISPARITY = 2.5
# research/05 §6.2 Stage B1 — >=3 matched takes in a row = deliberate refrain.
_REFRAIN_MIN_TAKES = 3
# research/05 §6.2 Stage D — "tie within 5% → keep the LATER take".
_TIE_DELTA = 0.05

# Tokens are lowercased and stripped of ASCII + Indic + CJK punctuation
# before matching (research/05 §5.4: normalise before any lexical match).
_STRIP_CHARS = (
    string.punctuation
    + "\u2026\u2018\u2019\u201c\u201d\u2013\u2014\u00b7"
    + "\u3002\uff0c\uff01\uff1f\uff1a\uff1b\u0964\u0965"  # 。 ， ！ ？ ： ； । ॥
    + " \t\r\n"
)

# Locale filler lexicon — PIPELINE.md list (en + hi/ta/te/ml/kn/bn chat
# fillers) plus the unambiguous English disfluencies from research/05 §6.3.
_FILLER_TOKENS = frozenset({
    "um", "umm", "uhm", "uh", "uhh", "er", "erm", "ah", "eh",
    "hmm", "hmmm", "hm", "mhm", "mm", "mmh",
    "matlab", "toh", "acha", "accha", "yaar", "yar", "na", "naa", "ki",
})
# Multi-word fillers matched longest-first so sub-phrases can't shadow them
# (research/05 §3.2 rule 6, §6.3 step 2).
_FILLER_PHRASES: tuple[tuple[str, ...], ...] = (
    ("you", "know"),
    ("i", "mean"),
)


# ----------------------------------------------------------------- range math
def _merge_ranges(
    ranges: list[tuple[float, float]], *, gap: float = 0.0
) -> list[tuple[float, float]]:
    """Sort and merge (optionally near-)overlapping ranges; drops empty ones."""
    cleaned = sorted((s, e) for s, e in ranges if e > s)
    out: list[list[float]] = []
    for s, e in cleaned:
        if out and s <= out[-1][1] + gap + 1e-9:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _clamp(ranges: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Clip ranges into [0, duration] and discard empties."""
    out = []
    for s, e in ranges:
        s2, e2 = max(0.0, s), min(duration, e)
        if e2 > s2:
            out.append((s2, e2))
    return out


def _complement(ranges: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Keep ranges = everything in [0, duration] not covered by ``ranges``.

    Union first, then complement — the composition rule for combining
    analyses (union of drops, then complement, then merge).
    """
    if duration <= 0:
        return []
    out: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in _merge_ranges(_clamp(ranges, duration)):
        if s > cursor + 1e-9:
            out.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration - 1e-9:
        out.append((cursor, duration))
    return out


def _norm(tok: str) -> str:
    """Lowercase + strip punctuation for lexicon/Jaccard matching."""
    return tok.lower().strip(_STRIP_CHARS)


# ------------------------------------------------------------------- silence
# Evidence guard — how much of the timeline the word clock actually speaks for.
#
# The silence analyzer is word-gap driven (research/05 §6.0: "one shared
# word-accurate timeline"), so it can only have *evidence* about regions ASR
# recognised. When recognised speech spans less than half the file, the
# majority of the timeline has zero evidence behind it, and cutting it would
# be deleting content on absent evidence rather than cutting proven dead air.
#
# Measured on the Agent A test reel (45 s instrumental veena performance,
# "Pesamale — Veena/Carnatic Notes"): ASR has no speech to hear and
# hallucinates 10 words inside the first 7.5 s → extent 0.16 → a naive run
# deletes 83 % of the video (keep ratio 0.17) and loses to auto-editor's
# loudness baseline, which keeps such a file whole (research/05 §6.1: that
# baseline is what we have to beat). Real speech sits far above the line:
# a talking reel spans ~0.9 of its timeline, and the sparse synthetic
# fixture used to unit-test this module spans 0.81.
_MIN_SPEECH_EXTENT = 0.5


def silence_keep_ranges(
    tr: Transcript,
    *,
    floor: float = 0.30,
    merge_gap: float = 0.80,
    margin: tuple[float, float] = (0.15, 0.35),
) -> tuple[list[tuple[float, float]], list[dict]]:
    """Word-gap driven dead-air removal → ``(keep, dropped)`` on the source timeline.

    Algorithm (research/05 §6.1 steps 3-5, expressed over ``tr.gaps()``):

    1. build speech pieces = words merged across gaps < ``merge_gap``
       (so a gap cuts only when it is >= ``merge_gap`` — "gaps < 0.80 s
       never cut" is the acceptance target, research/05 §6.1);
    2. drop speech pieces shorter than ``floor`` (coughs / false starts,
       timkulbaev's 0.30 s burst floor) — applied *before* margin expansion,
       because expansion adds ~0.5 s to every piece and would make ``floor``
       unreachable;
    3. expand each surviving piece by ``margin`` (0.15 s before its start,
       0.35 s after its end — auto-editor's asymmetric breathing room) and
       re-merge overlaps; leading/trailing silence is trimmed to the margin
       (timing.py: it is "real dead air a silence remover should cut");
    4. ``keep`` = the expanded pieces; ``dropped`` = their exact complement,
       tiled as audit entries ``{start, end, reason: "silence", gap}`` where
       ``gap`` is the original word-gap duration (>= merge_gap) that caused
       the cut, or the dropped width plus ``burst: true`` when a sub-floor
       speech island was swallowed.

    Conservative guarantees: no inter-word gap < ``merge_gap`` is ever cut,
    every cut keeps >= margin next to speech, an all-burst/no-word
    transcript keeps everything, and a transcript whose words span less than
    half the timeline keeps everything too (an ASR miss — or an instrumental
    track Whisper hallucinates over — must not delete the video).

    Raises:
        ValueError: negative ``floor``/``merge_gap``/``margin`` values.
    """
    T = float(tr.duration)
    if floor < 0 or merge_gap < 0 or margin[0] < 0 or margin[1] < 0:
        raise ValueError(
            f"floor/merge_gap/margin must be >= 0, got "
            f"floor={floor}, merge_gap={merge_gap}, margin={margin}"
        )
    if T <= 0:
        return [], []
    m_before, m_after = float(margin[0]), float(margin[1])

    words = sorted(tr.words, key=lambda w: w.s)
    if not words:
        # No recognised speech at all: dropping everything would delete the
        # whole video on an ASR miss or music-only file → keep all, no drops.
        return [(0.0, T)], []

    # 0. evidence guard — speech has to reach across the timeline before a
    #    gap analysis is trustworthy at all (research/05 §6.1 detector is
    #    the word clock; see _MIN_SPEECH_EXTENT for the measured numbers).
    attested = max(w.e for w in words) - words[0].s
    if attested / T < _MIN_SPEECH_EXTENT:
        return [(0.0, T)], []

    # 1. speech pieces: merge words across gaps < merge_gap.
    pieces: list[list[float]] = []
    for w in words:
        s = min(max(w.s, 0.0), T)
        e = min(max(w.e, 0.0), T)
        if e <= s:
            continue
        if pieces and s - pieces[-1][1] < merge_gap:
            pieces[-1][1] = max(pieces[-1][1], e)
        else:
            pieces.append([s, e])
    if not pieces:
        return [(0.0, T)], []

    # 2. burst rule: sub-floor speech islands are coughs/false starts.
    kept_pieces: list[tuple[float, float]] = []
    bursts: list[tuple[float, float]] = []
    for s, e in pieces:
        if e - s >= floor:
            kept_pieces.append((s, e))
        else:
            bursts.append((s, e))
    if not kept_pieces:
        # Every speech island is a sub-floor blip — cutting all of it is not
        # conservative (one long cough would remove the entire take).
        return [(0.0, T)], []

    # 3. margin expand + re-merge (auto-editor --margin 0.15s,0.35s).
    expanded: list[list[float]] = []
    for s, e in kept_pieces:
        ns, ne = max(0.0, s - m_before), min(T, e + m_after)
        if expanded and ns <= expanded[-1][1] + 1e-9:
            expanded[-1][1] = max(expanded[-1][1], ne)
        else:
            expanded.append([ns, ne])
    keep = [(s, e) for s, e in expanded if e > s]

    # 4. drops = complement, annotated for the audit trail.
    drop_spans = _complement(keep, T)
    gaps = tr.gaps()
    entries: list[dict] = []
    for ds, de in drop_spans:
        entry: dict = {
            "start": ds, "end": de, "reason": "silence",
            "gap": round(de - ds, 3),
        }
        host = next(
            (g for g in gaps if g[0] - 1e-6 <= ds and de <= g[1] + 1e-6), None
        )
        if host is not None:
            # drop sits inside one original word gap → report THAT gap's
            # duration (the auditable "why": it was >= merge_gap).
            entry["gap"] = round(host[1] - host[0], 3)
        elif any(bs < de and ds < be for bs, be in bursts):
            entry["burst"] = True  # a sub-floor speech island was swallowed
        entries.append(entry)
    return keep, entries


# -------------------------------------------------------------------- retake
def retake_keep_ranges(
    tr: Transcript,
    *,
    jaccard: float = 0.55,
    min_group_gap: float = 1.0,
) -> tuple[list[tuple[float, float]], list[dict]]:
    """Deterministic retake (double-take) removal → ``(keep, dropped)``.

    v1, no embeddings/models (research/05 §6.2 v1 scope: Stages A+C1+D):

    1. split ``tr.words`` into sentence groups on gaps >= ``min_group_gap``
       (1.0 s — Tribe-Video-Cleaner's sentence split, research/05 §6.2 A1);
    2. compare **consecutive** groups only; they are retakes of the same
       line iff token Jaccard >= ``jaccard`` (0.55) **and** word-count ratio
       >= 0.5, with safety filters: both groups >= 3 words and >= 0.5 s
       (A2), duration disparity <= 2.5x (B2), mean word conf >= 0.45 (B3 —
       an unreliable transcript must not auto-cut, EditBuddy failure mode
       #6);
    3. score = ``0.6 * mean(word conf) + 0.4 * closeness(dur, median group
       dur)`` where closeness = ``1 - |dur - median| / median`` clipped to
       [0, 1] (research/05 §6.2 D); keep the higher score, and on a tie
       within 0.05 keep the **later** take (TimeBolt "selects the last
       cut" + creator intuition);
    4. a chain of >= 3 matched groups is a deliberate refrain (B1) — keep
       all, drop nothing;
    5. dropped entries: ``{start, end, reason: "retake", kept_group,
       drop_group, score}`` spanning exactly the losing group's words
       (inter-group silence belongs to the silence analyzer).

    Conservative: a group with no clear repeat partner is never dropped,
    chains never cascade a drop onto an undecided neighbour, and a
    low-confidence transcript drops nothing at all.
    """
    T = float(tr.duration)
    if T <= 0:
        return [], []
    if jaccard < 0 or min_group_gap < 0:
        raise ValueError(
            f"jaccard/min_group_gap must be >= 0, got jaccard={jaccard}, "
            f"min_group_gap={min_group_gap}"
        )
    words = sorted(tr.words, key=lambda w: w.s)
    if not words:
        return [(0.0, T)], []

    # A1. sentence groups on gaps >= min_group_gap.
    groups: list[list[Word]] = []
    cur: list[Word] = [words[0]]
    for w in words[1:]:
        if w.s - cur[-1].e >= min_group_gap:
            groups.append(cur)
            cur = [w]
        else:
            cur.append(w)
    groups.append(cur)

    recs: list[dict] = []
    for idx, g in enumerate(groups):
        toks = {_norm(w.t) for w in g} - {""}
        recs.append({
            "idx": idx, "words": g,
            "start": g[0].s, "end": g[-1].e,
            "dur": max(g[-1].e - g[0].s, 1e-9),
            "n": len(g), "tok": toks,
            "conf": sum(w.conf for w in g) / len(g),
        })

    median_dur = statistics.median(r["dur"] for r in recs)

    def score(r: dict) -> float:
        """Take quality: 0.6*mean(word conf) + 0.4*closeness(dur, median).

        research/05 §6.2 Stage D (the two components v1 can compute without
        DNSMOS/loudness/LLM); closeness is clipped so a wildly long or short
        take can never outscore a normal one.
        """
        closeness = (
            max(0.0, 1.0 - abs(r["dur"] - median_dur) / median_dur)
            if median_dur > 0
            else 0.0
        )
        return 0.6 * r["conf"] + 0.4 * closeness

    def matches(a: dict, b: dict) -> bool:
        """True when a and b are two takes of one line.

        Gates in order: A2 (too short to be a retake), C1 lexical (word-count
        ratio >= 0.5 AND token Jaccard >= ``jaccard``), B2 (duration
        disparity) and B3 (transcript confidence) — research/05 §6.2. Any one
        gate failing means "not a proven repeat", and we only ever cut proven
        repeats.
        """
        if a["n"] < 3 or b["n"] < 3:            # A2: too short to be a retake
            return False
        if min(a["dur"], b["dur"]) < 0.5:       # A2
            return False
        if min(a["n"], b["n"]) / max(a["n"], b["n"]) < 0.5:
            return False                        # length ratio >= 0.5
        inter, uni = len(a["tok"] & b["tok"]), len(a["tok"] | b["tok"])
        if uni == 0 or inter / uni < jaccard:
            return False                        # token Jaccard >= jaccard
        if max(a["dur"], b["dur"]) / min(a["dur"], b["dur"]) > _RETAKE_LEN_DISPARITY:
            return False                        # B2 length-disparity guard
        if min(a["conf"], b["conf"]) < _RETAKE_CONF_FLOOR:
            return False                        # B3 confidence guard
        return True

    edges = [i for i in range(len(recs) - 1) if matches(recs[i], recs[i + 1])]

    dropped_recs: list[dict] = []
    entries: list[dict] = []
    i = 0
    while i < len(edges):
        j = i
        while j + 1 < len(edges) and edges[j + 1] == edges[j] + 1:
            j += 1
        chain = list(range(edges[i], edges[j] + 2))  # groups in this run
        if len(chain) >= _REFRAIN_MIN_TAKES:
            pass  # B1: >=3 matched takes in a row → intentional refrain, keep all
        else:
            a, b = chain[0], chain[1]
            sa, sb = score(recs[a]), score(recs[b])
            if abs(sa - sb) <= _TIE_DELTA:
                keep_r, drop_r = recs[b], recs[a]   # tie → LATER take
            elif sa > sb:
                keep_r, drop_r = recs[a], recs[b]
            else:
                keep_r, drop_r = recs[b], recs[a]
            dropped_recs.append(drop_r)
            entries.append({
                "start": drop_r["start"], "end": drop_r["end"],
                "reason": "retake",
                "kept_group": keep_r["idx"], "drop_group": drop_r["idx"],
                "score": round(score(keep_r), 4),
            })
        i = j + 1

    if not entries:
        return [(0.0, T)], []
    keep = _complement([(r["start"], r["end"]) for r in dropped_recs], T)
    return keep, entries


# -------------------------------------------------------------------- filler
def _filler_ok(words: list[Word], i: int, j: int, min_dur: float, pad: float) -> bool:
    """Duration floor + Descript "avoid harsh cuts" boundary safety.

    research/05 §6.3 step 4: skip a filler whose start is within 80 ms of
    the previous word's end, or whose end is within 80 ms of the next
    word's start — cutting there would clip a real word (or a breath).
    """
    span = words[j].e - words[i].s
    if span < min_dur:
        return False
    if i > 0 and (words[i].s - words[i - 1].e) < pad:
        return False
    if j + 1 < len(words) and (words[j + 1].s - words[j].e) < pad:
        return False
    return True


def filler_word_ranges(
    tr: Transcript, *, min_dur: float = 0.30, pad: float = 0.08
) -> list[tuple[float, float]]:
    """Ranges of unambiguous filler words to drop (source timeline).

    Locale lexicon (PIPELINE.md en + hi/ta/te/ml/kn/bn chat fillers,
    lowercased/punctuation-stripped match), phrases matched longest-first
    ("you know" before any single token), and two gates from research/05
    §6.3:

    * drop only when the filler spans >= ``min_dur`` (0.30 s) — shorter
      cuts leave jarring micro-gaps (timkulbaev);
    * skip when within ``pad`` (0.08 s = Descript's ~80 ms) of a
      neighbouring word boundary — "avoid harsh cuts".

    Returns non-overlapping, sorted ranges; ``[]`` when nothing is safely
    removable. Ambiguous real words (like/so/well/wait/…) are excluded on
    purpose — see module docstring.
    """
    if min_dur < 0 or pad < 0:
        raise ValueError(f"min_dur/pad must be >= 0, got min_dur={min_dur}, pad={pad}")
    words = sorted(tr.words, key=lambda w: w.s)
    if not words:
        return []
    norms = [_norm(w.t) for w in words]
    consumed = [False] * len(words)
    out: list[tuple[float, float]] = []

    for phrase in sorted(_FILLER_PHRASES, key=len, reverse=True):
        L = len(phrase)
        for i in range(len(words) - L + 1):
            if any(consumed[i : i + L]):
                continue
            if tuple(norms[i : i + L]) != phrase:
                continue
            if _filler_ok(words, i, i + L - 1, min_dur, pad):
                out.append((words[i].s, words[i + L - 1].e))
                for k in range(i, i + L):
                    consumed[k] = True

    for i, w in enumerate(words):
        if consumed[i] or norms[i] not in _FILLER_TOKENS:
            continue
        if _filler_ok(words, i, i, min_dur, pad):
            out.append((w.s, w.e))
            consumed[i] = True

    return _merge_ranges(out)


# ----------------------------------------------------------------- cutlist
def build_cutlist(
    video: str,
    tr: Transcript,
    *,
    silence: bool = True,
    retake: bool = False,
    fillers: bool = False,
    **silence_kw,
) -> Cutlist:
    """Compose the enabled analyses into one :class:`Cutlist`.

    Combination rule (PIPELINE): **union of drops → complement → merge**,
    which is exactly the intersection of the analyses' keep ranges — enabling
    more analyzers can only remove more, never resurrect a drop. Every
    analysis contributes its audit entries to ``cutlist.dropped``; the
    captions list stays empty (the captions stage fills it) and
    ``meta={"lang": tr.language}`` carries the transcript language.

    ``**silence_kw`` is forwarded to :func:`silence_keep_ranges` (retake and
    filler run on their researched defaults); ignored when ``silence`` is
    False.

    Fail-safe: if the union of drops somehow covers the whole timeline, the
    cutlist keeps everything and records a ``fail-safe`` audit entry rather
    than emitting an empty cut (an empty cut renders nothing and would crash
    downstream stages).

    Returns:
        Cutlist with fps/width/height/sample_rate copied from ``tr``.
    """
    T = float(tr.duration)
    drops: list[tuple[float, float]] = []
    entries: list[dict] = []

    if silence:
        _, sil_entries = silence_keep_ranges(tr, **silence_kw)
        drops.extend((e["start"], e["end"]) for e in sil_entries)
        entries.extend(sil_entries)
    if retake:
        _, rt_entries = retake_keep_ranges(tr)
        drops.extend((e["start"], e["end"]) for e in rt_entries)
        entries.extend(rt_entries)
    if fillers:
        for s, e in filler_word_ranges(tr):
            drops.append((s, e))
            entries.append({"start": s, "end": e, "reason": "filler"})

    if T <= 0:
        keep: list[tuple[float, float]] = []
    elif drops:
        keep = _complement(drops, T)
    else:
        keep = [(0.0, T)]

    if T > 0 and not keep:
        keep = [(0.0, T)]
        entries.append({
            "start": 0.0, "end": T, "reason": "fail-safe",
            "note": "union of drops covered the whole timeline; kept everything",
        })

    return Cutlist.from_keep_ranges(
        str(video), T, keep,
        fps=tr.fps, width=tr.width, height=tr.height,
        sample_rate=tr.sample_rate,
        dropped=entries,
        meta={"lang": tr.language},
    )
