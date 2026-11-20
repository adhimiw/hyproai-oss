# Caption presets & custom fonts

Source of truth: `core/hypro_core/caption_presets/*.json` (schema v1).
List them at runtime: `hypro presets`.

## The 8 built-ins

| id | vibe | font (size) | animation | highlight |
|---|---|---|---|---|
| tiktok-karaoke | classic social sweep, 4-word lines | Montserrat 76 bold | karaoke_sweep | #FFD400 |
| hormozi-pop | punchy coach style, big popping words | Montserrat 84 bold | word_pop (1.18x/90ms) | #FFD400 |
| mrbeast-bounce | huge uppercase, 3-word lines | Anton 96 uppercase | word_pop (1.25x/80ms) | #FFE600 |
| indic-karaoke | sweep tuned for Indic scripts | Mukta 80 bold | karaoke_sweep | #FF9E00 |
| slide-up | each line slides in | Montserrat 80 bold | line_slide | #FFD400 |
| neon-glow | cyan text, magenta glow | Poppins 72 bold | static (glow 3) | #FF4FD8 |
| boxed-classic | white on black box, calm | Inter 64 | static | #FFFFFF |
| minimal-fade | quiet, low-key, no flash | Inter 60 | static | #F2F2F2 |

All: base #FFFFFF, outline #000000, align=center, `karaokeTag=kf`
(libass `\kf` sweep), `maxWordsPerLine` 3–8, `pauseBreakMs` 350–600
(caption line breaks at pauses).

## Schema (every field the compiler reads)

```jsonc
{
  "id": "my-style",              // MUST equal the filename (my-style.json)
  "name": "My Style",            // shown in `hypro presets` and the website
  "version": 1,
  "refResolution": {"w": 1080, "h": 1920},   // fontSize is scaled from this
  "typography": {"fontFamily": "Montserrat", "fontFallback": ["Noto Sans"],
                 "fontSize": 76, "bold": true, "uppercase": false,
                 "letterSpacing": 0},
  "colors": {"base": "#FFFFFF", "highlight": "#FFD400", "outline": "#000000",
             "shadow": "#000000", "boxBackground": "#000000"},
  "layout": {"align": 2, "marginH": 60, "marginV": 420, "maxWordsPerLine": 4,
             "maxCharsPerLine": 20, "maxLineDurationMs": 2400,
             "pauseBreakMs": 350},
  "stroke": {"borderStyle": 1, "outlineWidth": 7, "shadowDepth": 3,
             "glowBlur": 0},
  "animation": {"mode": "static|word_pop|karaoke_sweep|line_slide",
                "karaokeTag": "kf", "popScale": 1.18, "popInMs": 90,
                "popOutMs": 0}
}
```

`_validate_preset()` (captions.py) fails fast with a readable error if a
group or key is missing — copy an existing file, don't build from scratch.

## How fonts resolve at burn time

Captions are burned with libass through ffmpeg's `ass=` filter. libass
resolves `fontFamily` via **fontconfig** and falls through
`fontFallback` per glyph — there is no fontsdir shipping. So:

- The font must be **installed on the machine running the burn**
  (`fc-list | grep -i montserrat`). Install: drop `.ttf/.otf` in
  `~/.fonts/` (or `/usr/share/fonts/`) then `fc-cache -f`.
- Missing everywhere → fontconfig substitutes a covering font; Indic
  scripts fall back to Noto Sans <script> if present. Boxes/TODO-font
  warnings in ffmpeg output mean nothing covers the script.
- The browser preview (website) uses the same `fontFamily` via CSS —
  same install rule applies to the machine serving the preview.

## Custom preset workflow

1. `cp core/hypro_core/caption_presets/minimal-fade.json core/hypro_core/caption_presets/my-style.json`
2. Edit `name`, colors, typography; **keep `"id": "my-style"`** (id must
   match the filename or `_validate_preset` rejects it).
3. Use it: `hypro edit video.mp4 --style my-style` or pick it in the
   website style dropdown (`GET /presets` reads the same directory).
4. Validate: `hypro presets` lists it; a bad file fails at load with the
   missing-field message, not mid-burn.
