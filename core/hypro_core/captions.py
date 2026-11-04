"""Caption stage — record-timeline word cues → styled ASS → burned video.

Pipeline position (core/PIPELINE.md, Agent B):

    group_cues(words) → finalize_cues(cues, lang) → compile_ass(cues, preset)
                                                 └→ burn(video, ass, out)

Styling spec: research/06-captions-styles.md. The gotchas that shaped this
file (all verified locally there, §4.3):

* ASS colors serialize as ``&HAABBGGRR`` (BGR, alpha ``00`` = opaque) while
  presets store human-order ``#RRGGBB`` — convert only at this boundary
  (06 §4.1 principle 2).
* ``PrimaryColour`` is the color DURING/AFTER a karaoke highlight and
  ``SecondaryColour`` the color BEFORE it (06 §2.2) — so only
  ``karaoke_sweep`` swaps them (highlight→primary, base→secondary); every
  other mode shows ``colors.base`` the whole time and paints the active word
  with a ``\\1c`` override.
* ``\\kf`` values are PER-WORD durations in centiseconds that ACCUMULATE
  from the Dialogue start — never absolute offsets (06 §2.2, §5.2 rule 2).
  They come from ``_alloc_cs`` (largest-remainder, 06 §4.3) so the sum
  equals the cue span exactly, each word ≥ 1cs so sub-centisecond tokens
  ("a", "to") don't vanish (rule 5).
* ASS time resolution is centiseconds; SRT/VTT are milliseconds (06 §5.2
  rule 4) — round ONCE from float seconds here, to cs, and stamp event
  times on cs multiples so the written timestamp and the ``\\kf`` sum agree
  exactly instead of drifting one centisecond.
* ASS has no per-word element: ``word_pop`` emits one Dialogue per word,
  each carrying the FULL line (so every event lays out identically) with the
  active word wrapped in ``\\t`` scale-pop + ``\\1c`` highlight tags — the
  ai-video-captions pattern we port (06 §2.2/§4.3). Events tile the cue so
  they never overlap (rule 3).
* Burn with ffmpeg's ``ass=`` filter, not ``subtitles=`` (06 §2.2):
  ``subtitles`` runs the file through libavformat and can flatten karaoke
  timing; ``ass`` hands it straight to libass (HarfBuzz shaping, verified
  on this machine).

Shipped presets — ``hypro_core/caption_presets/*.json``. Presets are data,
never code: a new style is a new JSON, the compiler owns the generators
(06 §4.1 principle 3). ``list_presets()`` returns the ids:

    hormozi-pop      word_pop       huge Montserrat, active word flashes #FFD400
    mrbeast-bounce   word_pop       Anton caps, thick outline, 125% bounce
    tiktok-karaoke   karaoke_sweep  short lines, left-to-right \\kf fill sweep
    indic-karaoke    karaoke_sweep  Mukta + Noto Sans Devanagari (shaping note
                                    in the JSON; group on whitespace tokens)
    minimal-fade     static         quiet Inter sentence, \\fad(400,300)
    neon-glow        static         Poppins, \\bord10 + \\blur3 glow edge
    boxed-classic    static         BorderStyle=3 opaque broadcast box
    slide-up         line_slide     \\move rise into place + \\fad

``preset_for()`` resolves loose style names ("Hormozi Pop", "tiktok",
"hormozi-pop.json") to a preset dict when the caller's name differs from the
file id.

The ``layout`` grouping budgets (06 §4.4: they live in the preset because
MrBeast-scale text needs 3 words/line while minimal styles take a whole
phrase) are consumed by ``group_cues`` callers:

    preset = preset_for("tiktok-karaoke")
    L = preset["layout"]
    cues = group_cues(words, max_words=L["maxWordsPerLine"],
                      max_chars=L["maxCharsPerLine"],
                      max_dur=L["maxLineDurationMs"] / 1000,
                      break_gap=L["pauseBreakMs"] / 1000)
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pysubs2

from .cutlist import CaptionCue
from .romanize import get_romanizer
from .romanize.cascade import SUPPORTED as ROMANIZER_LANGS
from .timing import Word

PRESET_DIR = Path(__file__).resolve().parent / "caption_presets"

#: animation.mode values the v1 compiler can emit. research/06 §4.3 defines
#: five generators; typewriter is intentionally not shipped yet — load-time
#: validation refuses any preset the compiler cannot render, which is 06
#: §4.5's CI gate ("fail on un-renderable tags") done at the door.
SUPPORTED_MODES = ("karaoke_sweep", "word_pop", "line_slide", "static")

#: Loose style names → canonical preset ids (preset_for / load_preset).
_ALIASES = {
    "hormozi": "hormozi-pop",
    "pop": "hormozi-pop",
    "mrbeast": "mrbeast-bounce",
    "mr-beast": "mrbeast-bounce",
    "bounce": "mrbeast-bounce",
    "tiktok": "tiktok-karaoke",
    "tik-tok": "tiktok-karaoke",
    "karaoke": "tiktok-karaoke",
    "minimal": "minimal-fade",
    "quiet": "minimal-fade",
    "neon": "neon-glow",
    "glow": "neon-glow",
    "boxed": "boxed-classic",
    "classic": "boxed-classic",
    "broadcast": "boxed-classic",
    "slide": "slide-up",
    "slide-up-captions": "slide-up",
    "indic": "indic-karaoke",
    "hindi": "indic-karaoke",
    "hinglish": "indic-karaoke",
    "thanglish": "indic-karaoke",
}

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Top-level preset groups and the fields compile_ass cannot work without.
_REQUIRED: dict[str, tuple[str, ...]] = {
    "id": (),
    "version": (),
    "refResolution": ("w", "h"),
    "typography": ("fontFamily", "fontSize", "bold", "uppercase"),
    "colors": ("base", "highlight", "outline", "shadow"),
    "layout": ("align", "marginH", "marginV"),
    "stroke": ("borderStyle", "outlineWidth", "shadowDepth", "glowBlur"),
    "animation": ("mode",),
}


# --------------------------------------------------------------------- presets
def list_presets() -> list[str]:
    """Sorted ids of the shipped presets (one JSON per style in
    ``caption_presets/``). Feeds ``hypro-core presets`` CLI listing."""
    if not PRESET_DIR.is_dir():
        raise FileNotFoundError(f"caption preset directory missing: {PRESET_DIR}")
    return sorted(p.stem for p in PRESET_DIR.glob("*.json"))


def _slug(name: str) -> str:
    """Normalize a preset name: case/whitespace/underscore insensitive,
    ``.json`` tolerated — so "Hormozi Pop" → "hormozi-pop" matches the id."""
    s = str(name).strip().lower()
    if s.endswith(".json"):
        s = s[: -len(".json")]
    return re.sub(r"[-\s_]+", "-", s).strip("-")


def _resolve_id(style_name: str) -> str:
    """Alias/display-name → canonical preset id, or FileNotFoundError with
    the available list (fail loudly, never a generic KeyError)."""
    if not str(style_name).strip():
        raise FileNotFoundError(
            f"empty preset name — available: {', '.join(list_presets())}")
    slug = _slug(style_name)
    ids = list_presets()
    if slug in ids:
        return slug
    target = _ALIASES.get(slug)
    if target in ids:
        return target
    raise FileNotFoundError(
        f"unknown caption preset {style_name!r} — available: {', '.join(ids)}")


def _validate_preset(preset: dict) -> None:
    """Schema gate (06 §4.1 principle 5 / §4.5): every field the compiler
    reads must exist and be well formed, so a bad preset fails at load with
    a readable message instead of mid-burn."""
    if not isinstance(preset, dict):
        raise ValueError(
            f"caption preset must be a dict, got {type(preset).__name__}")
    missing = [k for k in _REQUIRED if k not in preset]
    if missing:
        raise ValueError(f"caption preset missing fields: {', '.join(missing)}")
    for group, keys in _REQUIRED.items():
        if not keys:
            continue
        value = preset[group]
        if not isinstance(value, dict):
            raise ValueError(
                f"caption preset {group!r} must be an object, "
                f"got {type(value).__name__}")
        sub = [k for k in keys if k not in value]
        if sub:
            raise ValueError(f"caption preset {group!r} missing: {', '.join(sub)}")
    if preset["version"] != 1:
        raise ValueError(
            f"unsupported caption preset version {preset['version']!r} "
            "(this compiler understands version 1)")
    mode = preset["animation"]["mode"]
    if mode not in SUPPORTED_MODES:
        raise ValueError(
            f"animation.mode {mode!r} is not renderable by the v1 compiler "
            f"(supported: {', '.join(SUPPORTED_MODES)})")
    for key in ("base", "highlight", "outline", "shadow", "boxBackground"):
        color = preset["colors"].get(key)
        if color is not None and not _HEX_RE.match(str(color)):
            raise ValueError(
                f"colors.{key} must be #RRGGBB, got {color!r} "
                "(the compiler owns the &HAABBGGRR conversion, 06 §4.1)")
    align = preset["layout"]["align"]
    if not 1 <= int(align) <= 9:
        raise ValueError(f"layout.align must be an ASS alignment 1-9, got {align!r}")


def load_preset(name: str) -> dict:
    """Read ``caption_presets/<name>.json`` and validate it (PIPELINE
    contract). Case, whitespace, underscores and a ``.json`` suffix are
    tolerated; for aliases/display names use :func:`preset_for`."""
    pid = _resolve_id(name)
    path = PRESET_DIR / f"{pid}.json"
    if not path.is_file():
        raise FileNotFoundError(f"caption preset file missing: {path}")
    try:
        preset = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"caption preset {path} is not valid JSON: {e}") from e
    _validate_preset(preset)
    if preset["id"] != pid:
        raise ValueError(
            f"caption preset id {preset['id']!r} does not match its filename "
            f"{path.name!r}")
    return preset


def preset_for(style_name: str) -> dict:
    """Resolve a loose style name to a preset dict.

    Accepts anything a human/UI/CLI might hold — canonical id
    (``tiktok-karaoke``), display name (``Hormozi Pop``), alias (``hormozi``,
    ``mr beast``, ``indic``), any case, underscores, ``.json`` suffix — and
    returns the loaded, validated preset for :func:`compile_ass`. Unknown
    names raise FileNotFoundError listing what exists. Returns the dict, not
    the id; use :func:`list_presets` for ids."""
    return load_preset(_resolve_id(style_name))


# -------------------------------------------------------------------- grouping
def group_cues(
    words: list[Word],
    *,
    max_chars: int = 42,
    max_words: int = 8,
    max_dur: float = 4.0,
    break_gap: float = 0.6,
) -> list[CaptionCue]:
    """Greedy grouping on the RECORD timeline (caller remaps with
    ``remap_to_record`` first).

    A new cue starts when adding the next word would break any budget —
    characters, words, duration — or when the silence before it is
    ``>= break_gap``. Budgets exist so burned lines fit the frame and stay
    readable; pass a preset's ``layout`` numbers to get that preset's
    designed line shape (06 §4.4: grouping knobs ship IN the preset —
    ``maxLineDurationMs``/``pauseBreakMs`` are milliseconds here).

    Words are grouped whole, never split: an Indic token is multi-codepoint
    and splitting breaks HarfBuzz shaping (06 §5.2 rule 6). ``cue.words``
    keeps ``{"t","s","e"}`` so the karaoke/pop compilers stay 1:1 with the
    ASR timestamps. ``cue.text`` is still the native-script join;
    :func:`finalize_cues` romanizes it.
    """
    out: list[CaptionCue] = []
    # Stable sort: grouping must not depend on caller ordering, but words
    # sharing a start keep their ASR order.
    ordered = sorted(words, key=lambda w: (w.s, w.e))
    current: list[Word] = []

    def flush() -> None:
        if not current:
            return
        text = " ".join(w.t for w in current)
        out.append(CaptionCue(
            start=current[0].s,
            end=current[-1].e,
            text=text,
            native=text,
            words=[{"t": w.t, "s": w.s, "e": w.e} for w in current],
        ))

    for w in ordered:
        if current:
            gap = w.s - current[-1].e
            candidate = " ".join(x.t for x in current) + " " + w.t
            if (gap >= break_gap
                    or len(current) >= max_words
                    or len(candidate) > max_chars
                    or (w.e - current[0].s) > max_dur):
                flush()
                current = []
        current.append(w)
    flush()
    return out


def finalize_cues(cues: list[CaptionCue], lang: str | None) -> list[CaptionCue]:
    """Romanize cue text for display, keeping the original in ``native``.

    Every word in ``cue.words[i]["t]`` goes through
    ``Romanizer.romanize_token`` — a 1:1 token map, so karaoke/pop events
    still line up word-for-word with the ASR timestamps (PIPELINE
    contract). ``cue.native`` holds the pre-romanization text (the
    CaptionCue field) for reference/export. Returns NEW cues; the input
    list and its cues are untouched.

    ``lang`` accepts BCP-47-ish codes (``ta-IN`` → ``ta``). Languages
    outside the romanizer's six (hi/ta/te/ml/kn/bn) still romanize Indic
    tokens via per-token script detection, so a code-switched Tamil word in
    an "en" transcript doesn't leak native script into burned captions;
    non-Indic text passes through byte-identical (code-switch protection,
    research/04 §6 L0).
    """
    out: list[CaptionCue] = []
    for cue in cues:
        words = [dict(w) for w in cue.words]
        native = cue.text or " ".join(str(w.get("t", "")) for w in words)
        if lang:
            base = str(lang).replace("_", "-").split("-")[0].lower()
            target = base if base in ROMANIZER_LANGS else None
            romanizer = get_romanizer(target or "hi")
            if words:
                tokens: list[str] = []
                for w in words:
                    roman, _source = romanizer.romanize_token(
                        str(w.get("t", "")), target)
                    w["t"] = roman
                    tokens.append(roman)
                text = " ".join(tokens)
            else:
                # no word clock → whole-line romanize (punctuation-preserving)
                text = romanizer.romanize(native)
        else:
            text = native
        out.append(CaptionCue(
            start=cue.start, end=cue.end, text=text, native=native, words=words))
    return out


# ------------------------------------------------------------------ ASS utils
def _rgb(color_hex: str) -> tuple[int, int, int]:
    h = str(color_hex).lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _ass_color(color_hex: str) -> pysubs2.Color:
    """#RRGGBB → pysubs2.Color; alpha 0 writes as &H00BBGGRR (opaque)."""
    r, g, b = _rgb(color_hex)
    return pysubs2.Color(r, g, b, 0)


def _hex_tag(color_hex: str) -> str:
    """#RRGGBB → the ``&HBBGGRR`` form override tags expect (\\1c&H..&)."""
    r, g, b = _rgb(color_hex)
    return f"&H{b:02X}{g:02X}{r:02X}"


def _event_window(cue: CaptionCue) -> tuple[int, int]:
    """``(start_ms, span_cs)`` for a cue, rounded ONCE from float seconds.

    ASS renders centiseconds only (06 §2.2), so event times land on cs
    multiples: the written timestamp, the ``\\kf`` sum and the cue span then
    agree exactly instead of drifting a rounding centisecond (06 §5.2 rules
    2 + 4). A zero-length cue gets 1cs — an event with no duration would
    never appear."""
    start_cs = int(round(cue.start * 100))
    span_cs = int(round((cue.end - cue.start) * 100))
    if span_cs < 1:
        span_cs = 1
    return start_cs * 10, span_cs


def _alloc_cs(word_durs_s: list[float], span_cs: int) -> list[int]:
    """Per-word centiseconds summing to EXACTLY ``span_cs``.

    Durations, not offsets: ASS accumulates ``\\k``/``\\kf`` values from the
    Dialogue start (06 §2.2). Largest-remainder rounding (06 §4.3) keeps the
    total drift-free so the last word's highlight still lands on the cue end
    (§5.2 rule 2); every word gets ≥ 1cs so a 20 ms article doesn't collapse
    to a highlight libass may skip (rule 5)."""
    n = len(word_durs_s)
    if n == 0:
        return []
    if span_cs <= 0:
        return [0] * n
    if span_cs < n:
        # Degenerate cue (shorter than 1cs per word): keep the sum exact
        # rather than overshooting — the first span_cs words get 1cs.
        out = [0] * n
        for i in range(span_cs):
            out[i] = 1
        return out
    raw = [max(0.0, float(d) * 100.0) for d in word_durs_s]
    base = [max(1, int(r)) for r in raw]
    diff = span_cs - sum(base)
    if diff > 0:
        # Hand leftover centiseconds to the biggest fractional parts first,
        # round-robin so big inter-word gaps spread evenly.
        order = sorted(range(n), key=lambda i: raw[i] - int(raw[i]), reverse=True)
        i = 0
        while diff > 0:
            base[order[i % n]] += 1
            diff -= 1
            i += 1
    elif diff < 0:
        # max(1, …) pushed the total above the span (sub-centisecond words):
        # shave the longest words first, never below 1cs. Slack sum(base)-n
        # always covers |diff| here (span_cs >= n), so this terminates; the
        # guard makes a future edit fail loudly instead of looping forever.
        steps = 0
        while diff < 0:
            idx = max(range(n), key=lambda i: base[i])
            if base[idx] <= 1 or steps > span_cs + n:
                raise RuntimeError(
                    "karaoke duration allocation could not converge "
                    f"(span_cs={span_cs}, words={n})")
            base[idx] -= 1
            diff += 1
            steps += 1
    if sum(base) != span_cs:  # pragma: no cover - defensive
        raise RuntimeError(
            f"karaoke durations sum to {sum(base)}, expected {span_cs}")
    return base


def _display_tokens(cue: CaptionCue, preset: dict) -> list[str]:
    """Word tokens as they will be rendered (uppercase flag applied — Tamil
    has no case, so Indic tokens pass through untouched)."""
    upper = bool(preset["typography"].get("uppercase", False))
    tokens = [str(w.get("t", "")) for w in cue.words]
    return [t.upper() for t in tokens] if upper else tokens


def _display_text(cue: CaptionCue, preset: dict) -> str:
    """Full cue line as it will be rendered (falls back to the word clock
    when ``cue.text`` is empty)."""
    text = cue.text or " ".join(str(w.get("t", "")) for w in cue.words)
    return text.upper() if preset["typography"].get("uppercase") else text


def _line_prefix(
    preset: dict, scale: float, *, fade: tuple[int, int] | None = None
) -> str:
    """Override tags applying to a whole Dialogue: line ``\\fad`` and the
    ``\\blur`` glow (glowBlur is a pixel radius, so it scales with output
    resolution like every other metric, 06 §4.5)."""
    if fade is None:
        anim = preset["animation"]
        fade = (int(anim.get("fadeInMs", 0) or 0),
                int(anim.get("fadeOutMs", 0) or 0))
    parts: list[str] = []
    if fade[0] or fade[1]:
        parts.append(f"\\fad({fade[0]},{fade[1]})")
    glow = float(preset["stroke"].get("glowBlur", 0) or 0)
    if glow > 0:
        parts.append(f"\\blur{max(1, int(round(glow * scale)))}")
    return "{" + "".join(parts) + "}" if parts else ""


def _build_style(preset: dict, scale: float) -> pysubs2.SSAStyle:
    """Preset → the one ASS Style (metrics scaled by target_h/ref_h, 06 §4.5).

    Color roles follow 06 §2.2: karaoke_sweep needs Primary=highlight
    (during/after) and Secondary=base (before); every other mode is
    single-color (base) with the active word recolored by an override tag."""
    t, c = preset["typography"], preset["colors"]
    o, layout = preset["stroke"], preset["layout"]
    st = pysubs2.SSAStyle()
    st.fontname = str(t["fontFamily"])
    # fontFallback is data for the browser preview / fontsdir shipping —
    # libass picks a covering font per glyph via fontconfig fallback (06 §2.5)
    st.fontsize = max(1, int(round(t["fontSize"] * scale)))
    st.bold = bool(t["bold"])
    st.spacing = int(round(float(t.get("letterSpacing", 0) or 0) * scale))
    if preset["animation"]["mode"] == "karaoke_sweep":
        st.primarycolor = _ass_color(c["highlight"])
        st.secondarycolor = _ass_color(c["base"])
    else:
        st.primarycolor = _ass_color(c["base"])
        st.secondarycolor = _ass_color(c["base"])
    st.outlinecolor = _ass_color(c["outline"])
    # BorderStyle=3 fills an opaque box from BackColour (broadcast bar);
    # otherwise BackColour is the drop shadow (06 §4.2 colors.shadow).
    if int(o["borderStyle"]) == 3:
        st.backcolor = _ass_color(c.get("boxBackground") or c["shadow"])
    else:
        st.backcolor = _ass_color(c["shadow"])
    st.borderstyle = int(o["borderStyle"])
    st.outline = int(round(float(o["outlineWidth"]) * scale))
    st.shadow = int(round(float(o["shadowDepth"]) * scale))
    st.alignment = int(layout["align"])
    st.marginl = st.marginr = int(round(float(layout["marginH"]) * scale))
    st.marginv = int(round(float(layout["marginV"]) * scale))
    return st


def _anchor(align: int, width: int, height: int, m_h: int, m_v: int) -> tuple[int, int]:
    """PlayRes pixel position of an ``\\an<align>`` anchor — needed to turn
    layout margins into ``\\move`` coordinates for line_slide."""
    col, row = (align - 1) % 3, (align - 1) // 3
    x = m_h if col == 0 else (width - m_h if col == 2 else width // 2)
    y = m_v if row == 0 else (height - m_v if row == 2 else height // 2)
    return x, y


# ------------------------------------------------------------------- generators
def _karaoke_event(cue: CaptionCue, preset: dict, scale: float) -> pysubs2.SSAEvent:
    """karaoke_sweep: ONE Dialogue per cue, ``\\kf`` per word.

    Durations come from the word clock, allocated by ``_alloc_cs`` so they
    sum to the cue span exactly (06 §5.2 rule 2). Without a word clock the
    whole cue sweeps as a single block — still exact."""
    start_ms, span_cs = _event_window(cue)
    tag = str(preset["animation"].get("karaokeTag", "kf")).lstrip("\\")
    if tag not in ("k", "kf", "K", "ko"):
        raise ValueError(
            f"animation.karaokeTag must be one of k/kf/K/ko, got {tag!r}")
    if cue.words:
        tokens = _display_tokens(cue, preset)
        durs = [max(0.0, float(w["e"]) - float(w["s"])) for w in cue.words]
        parts = [
            f"{{\\{tag}{cs}}}{tok} "
            for tok, cs in zip(tokens, _alloc_cs(durs, span_cs))
        ]
        body = "".join(parts).rstrip()
    else:
        body = f"{{\\{tag}{span_cs}}}{_display_text(cue, preset)}"
    return pysubs2.SSAEvent(
        start=start_ms, end=start_ms + span_cs * 10, layer=0,
        text=_line_prefix(preset, scale) + body)


def _pop_events(cue: CaptionCue, preset: dict, scale: float) -> list[pysubs2.SSAEvent]:
    """word_pop: one Dialogue per word (ASS has no per-word element, 06
    §2.2), each carrying the FULL line so every event lays out identically,
    with the active word wrapped in ``\\1c`` highlight + ``\\t`` scale-pop
    tags (the ai-video-captions pattern, 06 §4.3).

    Events tile ``[word_i.s, word_{i+1}.s)`` — contiguous, never overlapping
    (06 §5.2 rule 3) — so the line is visible for the whole cue with exactly
    one word popping at a time. The line ``\\fad`` rides the first event
    (in) and the last (out); middle events must not fade or the line would
    flicker between word slots."""
    anim = preset["animation"]
    start_ms, span_cs = _event_window(cue)
    end_ms = start_ms + span_cs * 10
    tokens = _display_tokens(cue, preset)
    if not tokens:
        # no word clock → can't time pops; one static line, fade intact
        return [pysubs2.SSAEvent(
            start=start_ms, end=end_ms, layer=0,
            text=_line_prefix(preset, scale) + _display_text(cue, preset))]

    highlight = _hex_tag(preset["colors"]["highlight"])
    pop_scale = int(round(float(anim.get("popScale", 1.0)) * 100))
    pop_in = int(anim.get("popInMs", 0) or 0)
    pop_out = int(anim.get("popOutMs", 0) or 0)
    transform = ""
    if pop_in > 0:
        transform += f"\\t(0,{pop_in},\\fscx{pop_scale}\\fscy{pop_scale})"
    if pop_out > 0:
        transform += f"\\t({pop_in},{pop_in + pop_out},\\fscx100\\fscy100)"
    fade_in = int(anim.get("fadeInMs", 0) or 0)
    fade_out = int(anim.get("fadeOutMs", 0) or 0)

    # Slot boundaries: word starts (cs-rounded) with the cue window as the
    # outer frame; clamped monotonic so pathological <10ms word starts can
    # never produce overlapping events.
    starts = [start_ms] + [
        int(round(float(w["s"]) * 100)) * 10 for w in cue.words[1:]
    ]
    events: list[pysubs2.SSAEvent] = []
    prev_end = start_ms
    for i, token in enumerate(tokens):
        slot_start = max(starts[i], prev_end)
        slot_end = starts[i + 1] if i + 1 < len(tokens) else end_ms
        if slot_end <= slot_start:
            slot_end = slot_start + 10
        prev_end = slot_end

        parts = [
            f"{{\\1c{highlight}&{transform}}}{tok}{{\\r}}" if j == i else tok
            for j, tok in enumerate(tokens)
        ]
        text = " ".join(parts)
        if i == 0 and i == len(tokens) - 1:
            fade = (fade_in, fade_out)
        elif i == 0:
            fade = (fade_in, 0)
        elif i == len(tokens) - 1:
            fade = (0, fade_out)
        else:
            fade = (0, 0)
        prefix = _line_prefix(preset, scale, fade=fade)
        events.append(pysubs2.SSAEvent(
            start=slot_start, end=slot_end, layer=0, text=prefix + text))
    return events


def _static_event(cue: CaptionCue, preset: dict, scale: float) -> pysubs2.SSAEvent:
    """static: one Dialogue per cue, ``\\fad`` for entry/exit, optional
    ``\\blur`` glow (neon-glow, 06 §4.4 row 5)."""
    start_ms, span_cs = _event_window(cue)
    return pysubs2.SSAEvent(
        start=start_ms, end=start_ms + span_cs * 10, layer=0,
        text=_line_prefix(preset, scale) + _display_text(cue, preset))


def _slide_event(
    cue: CaptionCue, preset: dict, width: int, height: int, scale: float
) -> pysubs2.SSAEvent:
    """line_slide: the line rises into place — ``\\move`` from slidePx below
    the anchor up to it, plus the line ``\\fad`` (06 §4.3). Coordinates come
    from the layout anchor so margins/alignment carry over."""
    anim, layout = preset["animation"], preset["layout"]
    start_ms, span_cs = _event_window(cue)
    m_h = int(round(float(layout["marginH"]) * scale))
    m_v = int(round(float(layout["marginV"]) * scale))
    x, y = _anchor(int(layout["align"]), width, height, m_h, m_v)
    slide = max(0, int(round(float(anim.get("slidePx", 60)) * scale)))
    move = f"{{\\move({x},{y + slide},{x},{y})}}"
    return pysubs2.SSAEvent(
        start=start_ms, end=start_ms + span_cs * 10, layer=0,
        text=_line_prefix(preset, scale) + move + _display_text(cue, preset))


# ---------------------------------------------------------------------- compile
def compile_ass(
    cues: list[CaptionCue],
    preset: dict,
    *,
    width: int,
    height: int,
    out_path: str | None = None,
) -> str:
    """Convert record-time cues to a styled ASS file (returns the text).

    One generator per preset ``animation.mode`` (06 §4.1 principle 3 — new
    style = new JSON, never new renderer code): karaoke_sweep (``\\kf``
    centiseconds), word_pop (duplicate Dialogue per word with ``\\t`` scale
    pop + ``\\fad``), line_slide (``\\move`` + ``\\fad``), static. Colors,
    stroke, margins, alignment, uppercase and the grouping-independent
    typography all come from the preset; metrics scale by ``height /
    refResolution.h`` with PlayRes set to the target size (06 §4.5).

    Writes ``out_path`` when given; the same text is returned either way so
    callers can round-trip it through pysubs2 or the export sidecars."""
    _validate_preset(preset)
    if width <= 0 or height <= 0:
        raise ValueError(
            f"compile_ass needs a positive target size, got {width}x{height}")
    ref = preset["refResolution"]
    scale = height / float(ref["h"])

    ss = pysubs2.SSAFile()
    ss.info["PlayResX"], ss.info["PlayResY"] = str(width), str(height)
    ss.styles["Default"] = _build_style(preset, scale)

    mode = preset["animation"]["mode"]
    for cue in cues:
        if mode == "karaoke_sweep":
            ss.events.append(_karaoke_event(cue, preset, scale))
        elif mode == "word_pop":
            ss.events.extend(_pop_events(cue, preset, scale))
        elif mode == "line_slide":
            ss.events.append(_slide_event(cue, preset, width, height, scale))
        elif mode == "static":
            ss.events.append(_static_event(cue, preset, scale))
        else:  # pragma: no cover - _validate_preset makes this unreachable
            raise ValueError(f"unsupported animation.mode {mode!r}")

    text = ss.to_string("ass")
    if out_path is not None:
        path = Path(out_path)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return text


def _escape_filter_path(path: str) -> str:
    """Escape a filename for ffmpeg's filtergraph option syntax — ``:`` and
    ``'`` delimit options/quotes and `,` separates filters, all of which
    would otherwise truncate the path (06 §2.2: this is the ``ass=`` filter)."""
    return (path.replace("\\", "\\\\").replace(":", "\\:")
               .replace("'", "\\'").replace(",", "\\,"))


def burn(video: str, ass_path: str, out: str, *, crf: int = 18) -> str:
    """Hard-burn the ASS onto the video: one ffmpeg pass, ``ass=`` filter →
    libass (HarfBuzz), single re-encode (06 §2.2/§3 — ``ass=`` hands the
    file straight to libass; ``subtitles=`` can flatten karaoke timing).

    Audio is stream-copied (the burn touches only pixels). Standalone
    convenience — the render stage can also fold the burn into its concat
    pass. Returns the output path; raises FileNotFoundError/RuntimeError
    with readable messages."""
    for path, label in ((video, "video"), (ass_path, "ass")):
        if not Path(path).is_file():
            raise FileNotFoundError(f"{label} file not found: {path}")
    dest = Path(out)
    if dest.parent and not dest.parent.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vf", f"ass={_escape_filter_path(str(ass_path))}",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-c:a", "copy", "-movflags", "+faststart",
        str(dest),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600)
    except FileNotFoundError as e:
        raise RuntimeError("ffmpeg not found — install ffmpeg (libass build)") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg burn timed out after 3600s: {video}") from e
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-800:]
        raise RuntimeError(
            f"ffmpeg burn failed (exit {proc.returncode}) for {video}: {tail}")
    if not dest.is_file():
        raise RuntimeError(f"ffmpeg reported success but wrote nothing: {dest}")
    return str(dest)
