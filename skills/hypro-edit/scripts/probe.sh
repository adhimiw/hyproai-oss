#!/usr/bin/env bash
# probe.sh — one-look media summary for the hypro-edit analyse step.
# Usage: bash probe.sh <video-or-audio>
set -euo pipefail
f="${1:?usage: probe.sh <file>}"
[ -f "$f" ] || { echo "NOT FOUND: $f" >&2; exit 1; }
command -v ffprobe >/dev/null || { echo "ffprobe missing — install ffmpeg" >&2; exit 2; }

dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null || echo 0)
size=$(ffprobe -v error -show_entries format=size -of csv=p=0 "$f" 2>/dev/null || echo 0)
echo "file    : $f"
echo "duration: ${dur%.*}s"
echo "size    : $(( size / 1024 / 1024 )) MB"

ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,avg_frame_rate -of csv=p=0 "$f" 2>/dev/null \
  | awk -F, '{printf "video   : %s %sx%s @ %s fps\n", $1, $2, $3, $4}' || echo "video   : none"
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,sample_rate,channels -of csv=p=0 "$f" 2>/dev/null \
  | awk -F, '{printf "audio   : %s %s Hz %sch\n", $1, $2, $3}' || true
ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$f" 2>/dev/null | grep -q . \
  && echo "has_audio: yes" || echo "has_audio: NO (pipeline will cut nothing, captions only)"
