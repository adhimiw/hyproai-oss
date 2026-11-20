# NLE export — artifacts & import recipes

`hypro edit` writes `nle/` after every run (`--no-nle` to skip); export
an existing cutlist with `hypro export cutlist.json --out nle/ --name edit`.
Every folder ships `HOWTO.txt` with these instructions baked in.

## Artifacts

| file | target | notes |
|---|---|---|
| `<name>.xml` | **Premiere Pro**, **DaVinci Resolve** (v17+) | FCP7 XML (xmeml v4) |
| `<name>.fcpxml` | **Final Cut Pro 10.4+** | needs `otio-fcpx-xml-adapter` (declared in core/pyproject.toml; HOWTO says NOT installed if missing) |
| `<name>.otio` | Resolve 17+, future-proofing | native OpenTimelineIO JSON |
| `<name>.jsx` | **After Effects** | File ▸ Scripts ▸ Run Script File |
| `<name>.srt` | all NLEs as caption track | FCP7 XML has no subtitle track |
| `tracks/*.edl` | belt & braces | CMX3600, one per track |
| `cutlist.json` | ground truth | diffable, re-export source |

## Import steps

- **Premiere**: File ▸ Import ▸ `<name>.xml` (V1=a-roll, V2=b-roll,
  A1=music, A2=voice) + `<name>.srt` → caption track.
- **Resolve**: File ▸ Import ▸ Timeline ▸ `<name>.xml` (or `.otio`);
  File ▸ Import ▸ Subtitle ▸ `<name>.srt`.
- **Final Cut Pro**: File ▸ Import ▸ XML… ▸ `<name>.fcpxml`; captions:
  File ▸ Import ▸ Captions… ▸ `<name>.srt`.
- **After Effects**: File ▸ Scripts ▸ Run Script File ▸ `<name>.jsx`
  (imports every cut into a new comp, source-trimmed). Or import the
  Premiere XML in Premiere, save, then File ▸ Import ▸ Premiere Project.

## Track layout & A/V sync

- First `<track>` under `<video>` = V1 (jump cuts = separate clipitems
  sharing one `<file id>`), then V2 = b-roll; `<audio>`: A1 = music,
  A2 = **voice**.
- The a-roll audio is **mirrored onto A2 with identical in/out** whenever
  the source has audio (`meta.has_audio`) — OTIO's FCP7 writer emits no
  `<link>`, so without this every import would be silent. Playback is
  frame-synced out of the box; V1↔A2 arrive *unlinked* — select both and
  Link (Ctrl/Cmd+L) so they move together.
- The sequence starts at 00:00:00:00 so the SRT lands on the right frames.
- FCPXML anchors A2 as a lane child (`lane="-2"`), offset in the parent
  clip's local timeline — matches FCP's own export convention; the asset
  carries `hasAudio="1"`.

## Known gaps (research 08)

- Track *names* aren't carried by FCP7 XML (V1/A1 only) — embed meaning
  in clip names if the user cares.
- No transitions/effects (a cut list doesn't need them); no `<link>`/
  `<sourcetrack>` (see above); AAF would carry links but isn't shipped.
- EDLs: one file per track (`cmx_3600` rejects multi-video timelines).
- `.fcpxml` format `name`/`width`/`height` stay empty when ffprobe can't
  resolve the source path at export time (e.g. container paths) — FCP
  resolves media on relink; harmless.
