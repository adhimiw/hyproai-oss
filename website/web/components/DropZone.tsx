"use client";

import { useId, useState } from "react";
import { formatBytes } from "@/lib/format";

interface DropZoneProps {
  file: File | null;
  error: string | null;
  disabled?: boolean;
  onFile: (file: File) => void;
}

/**
 * Drag-and-drop zone with a keyboard-accessible file input fallback.
 * The input is visually hidden but focusable — the visible label is its
 * click target, and focusing the input outlines the whole zone
 * (`:focus-within` in globals.css).
 */
export default function DropZone({ file, error, disabled, onFile }: DropZoneProps) {
  const inputId = useId();
  const [dragging, setDragging] = useState(false);

  return (
    <div
      className={`dropzone${dragging ? " is-drag" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        if (disabled) return;
        const dropped = event.dataTransfer.files?.[0];
        if (dropped) onFile(dropped);
      }}
    >
      <label className="dropzone-label" htmlFor={inputId}>
        <span className="dropzone-cta">Choose a video file</span>
        <span className="dropzone-sub">or drop one anywhere in this box</span>
      </label>
      <input
        id={inputId}
        className="sr-file-input"
        type="file"
        accept="video/*"
        disabled={disabled}
        onChange={(event) => {
          const picked = event.target.files?.[0];
          // reset so re-picking the same file after a fix fires onChange
          event.target.value = "";
          if (picked) onFile(picked);
        }}
      />
      {file && (
        <p className="file-chip" title={file.name}>
          {file.name} · {formatBytes(file.size)}
        </p>
      )}
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
