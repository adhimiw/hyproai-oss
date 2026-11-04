// mock/sidecar.mjs — a tiny, dev-only fake of the sidecar described in
// website/API_CONTRACT.md, for developing the UI without the real Python
// service. NOT imported by any app code and NOT part of the production build.
//
//   npm run mock                 # http://localhost:8787
//   MOCK_PORT=9000 npm run mock
//   MOCK_GEMINI=1 npm run mock   # pretend the server has GEMINI_API_KEY
//
// Then either `next dev` (rewrite proxies here by default) or
// NEXT_PUBLIC_SIDECAR_ORIGIN=http://localhost:8787 next dev.
// Note: edited.mp4 bytes are a placeholder header — the results-page <video>
// will not play them; stats/downloads behave normally.

import http from "node:http";
import { randomUUID } from "node:crypto";

const PORT = Number(process.env.MOCK_PORT || 8787);
const GEMINI_KEY = process.env.MOCK_GEMINI === "1";
const FAIL_JOB = process.env.MOCK_FAIL === "1";

const STAGES = [
  { stage: "probe", from: 0, to: 2, ms: 600, label: "probing media" },
  { stage: "transcribe", from: 2, to: 45, ms: 5000, label: "transcribing audio" },
  { stage: "analyze", from: 45, to: 52, ms: 900, label: "building cutlist" },
  { stage: "captions", from: 52, to: 60, ms: 900, label: "grouping cues" },
  { stage: "audio", from: 60, to: 72, ms: 1200, label: "enhancing audio" },
  { stage: "render", from: 72, to: 92, ms: 3500, label: "rendering edit" },
  { stage: "export", from: 92, to: 100, ms: 1000, label: "exporting nle project" },
];

const PRESETS = [
  { id: "tiktok-karaoke", mode: "karaoke_sweep", font: "Montserrat", size: 76 },
  { id: "clean-lower-third", mode: "block_line", font: "Inter", size: 54 },
  { id: "pop-bounce", mode: "word_pop", font: "Poppins", size: 68 },
  { id: "minimal-subs", mode: "sentence", font: "IBM Plex Sans", size: 46 },
];

const MAX_BYTES = 500 * 1024 * 1024;
const uploads = new Map();
const jobs = new Map();

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function sendJson(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json", ...CORS });
  res.end(JSON.stringify(body));
}

function sendError(res, status, message) {
  sendJson(res, status, { error: message });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function writeEvent(res, name, data) {
  res.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
}

function emit(job, name, data) {
  job.log.push({ name, data });
  for (const sub of job.subs) writeEvent(sub, name, data);
}

function snapshot(job) {
  return {
    id: job.id,
    status: job.status,
    stage: job.stage,
    percent: job.percent,
    message: job.message,
    error: job.error,
    result: job.result,
  };
}

function finishJob(job, request) {
  job.status = "done";
  job.stage = "done";
  job.percent = 100;
  job.message = "done";
  job.result = {
    edited: "edited.mp4",
    srt: "captions.srt",
    ass: "captions.ass",
    nle_zip: "nle.zip",
    cutlist: "cutlist.json",
    words: "words.json",
    stats: {
      source_duration: 30.01,
      kept: 24.6,
      keep_ratio: 0.819,
      ranges: 7,
      cues: 42,
      dropped: { silence: 5, retake: 2 },
      brain: request.options.brain,
      lang: request.lang ?? "ta",
      style: request.style,
    },
  };
  emit(job, "done", { result: job.result });
  for (const sub of job.subs) sub.end();
  job.subs.clear();
}

function failJob(job, message) {
  job.status = "error";
  job.error = message;
  job.message = message;
  emit(job, "error", { error: message });
  for (const sub of job.subs) sub.end();
  job.subs.clear();
}

function runStage(job, request, index) {
  if (job.status === "error") return;
  if (index >= STAGES.length) {
    finishJob(job, request);
    return;
  }
  const stage = STAGES[index];
  job.stage = stage.stage;
  job.percent = stage.from;
  job.message = stage.label;
  emit(job, "progress", { stage: stage.stage, percent: stage.from, message: stage.label });

  const reusing = stage.stage === "transcribe" && Boolean(request.reuse_words_of);
  if (reusing) {
    emit(job, "log", { stage: "transcribe", message: "reusing cached transcript" });
  } else if (stage.stage === "transcribe" && request.options.brain === "gemini" && !GEMINI_KEY) {
    emit(job, "log", {
      stage: "transcribe",
      message: "gemini unavailable — falling back to local",
    });
  }
  if (stage.stage === "captions" && request.options.burn) {
    emit(job, "log", { stage: "captions", message: `burning ${request.style} captions` });
  }

  const ticks = 4;
  let tick = 0;
  const every = Math.max(80, Math.round(stage.ms / ticks));
  const timer = setInterval(() => {
    tick += 1;
    const percent = Math.round(stage.from + ((stage.to - stage.from) * tick) / ticks);
    job.percent = percent;
    job.message = reusing && tick < ticks ? "reusing cached transcript" : stage.label;
    emit(job, "progress", { stage: stage.stage, percent, message: job.message });
    if (tick >= ticks) {
      clearInterval(timer);
      if (FAIL_JOB && stage.stage === "render") {
        failJob(job, "render failed: ffmpeg exited with code 1 (mock failure)");
        return;
      }
      runStage(job, request, index + 1);
    }
  }, every);
}

async function handleUpload(req, res) {
  const declared = Number(req.headers["content-length"] || 0);
  if (declared > MAX_BYTES) {
    sendError(res, 413, "file too large — 500 MB max (mock)");
    return;
  }
  const body = await readBody(req);
  if (body.length > MAX_BYTES) {
    sendError(res, 413, "file too large — 500 MB max (mock)");
    return;
  }
  const head = body.subarray(0, 4096).toString("latin1");
  const match = head.match(/filename="([^"]*)"/);
  const name = match && match[1] ? match[1] : "upload.bin";
  if (!/\.(mp4|mov|mkv|webm|avi|m4v|mpg|mpeg|3gp)$/i.test(name)) {
    sendError(res, 400, "no video stream found in upload (mock)");
    return;
  }
  const id = randomUUID();
  uploads.set(id, { id, name, size: body.length });
  sendJson(res, 201, {
    id,
    name,
    duration: 30.01,
    width: 1080,
    height: 1920,
    fps: 30.0,
    has_audio: true,
    size: body.length,
  });
}

async function handleJobCreate(req, res) {
  const body = await readBody(req);
  let payload;
  try {
    payload = JSON.parse(body.toString("utf8"));
  } catch {
    sendError(res, 400, "invalid JSON body");
    return;
  }
  const uploadId = payload?.upload_id;
  if (typeof uploadId !== "string" || !uploads.has(uploadId)) {
    sendError(res, 404, "unknown upload id");
    return;
  }
  const style = payload?.style;
  if (typeof style !== "string" || !PRESETS.some((p) => p.id === style)) {
    sendError(res, 400, "unknown style — see GET /presets");
    return;
  }
  const lang = payload?.lang ?? null;
  if (lang !== null && !["hi", "ta", "te", "ml", "kn", "bn"].includes(lang)) {
    sendError(res, 400, "unknown language");
    return;
  }
  const options = {
    silence: true,
    retake: false,
    fillers: false,
    burn: true,
    enhance_audio: true,
    brain: "local",
    model: "small",
    ...(payload?.options ?? {}),
  };
  const id = randomUUID();
  const job = {
    id,
    status: "queued",
    stage: "probe",
    percent: 0,
    message: "queued",
    error: null,
    result: null,
    log: [],
    subs: new Set(),
  };
  jobs.set(id, job);
  sendJson(res, 202, { id });
  const request = { lang, style, options, reuse_words_of: payload?.reuse_words_of ?? null };
  setImmediate(() => {
    job.status = "running";
    runStage(job, request, 0);
  });
}

function handleEvents(req, res, job) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    ...CORS,
  });
  // full replay first — this is what makes refresh/reconnect safe
  for (const entry of job.log) writeEvent(res, entry.name, entry.data);

  const terminal = job.status === "done" || job.status === "error";
  if (terminal) {
    res.end();
    return;
  }
  job.subs.add(res);
  const heartbeat = setInterval(() => {
    if (!res.writableEnded) res.write(": ping\n\n");
  }, 15000);
  req.on("close", () => {
    clearInterval(heartbeat);
    job.subs.delete(res);
  });
}

const VIDEO_HEADER = (() => {
  const buf = Buffer.alloc(64 * 1024);
  // ftyp box so it at least smells like an mp4 (not actually playable)
  buf.writeUInt32BE(24, 0);
  buf.write("ftyp", 4);
  buf.write("isom", 8);
  buf.writeUInt32BE(512, 12);
  buf.write("isomiso2mp41", 16);
  return buf;
})();

function handleFile(res, job, name, download) {
  if (job.status !== "done" || !job.result) {
    sendError(res, 404, "job has no files yet");
    return;
  }
  const allowed = {
    "edited.mp4": { type: "video/mp4", body: VIDEO_HEADER },
    "captions.srt": {
      type: "text/plain; charset=utf-8",
      body: Buffer.from(
        "1\n00:00:00,000 --> 00:00:02,400\nvanakkam, idhu oru mock caption\n\n2\n00:00:02,600 --> 00:00:05,000\nsecond line here\n",
      ),
    },
    "captions.ass": {
      type: "text/plain; charset=utf-8",
      body: Buffer.from(
        "[Script Info]\nTitle: mock\nScriptType: v4.00+\n\n[Events]\nFormat: Layer, Start, End, Style, Name, Text\nDialogue: 0,0:00:00.00,0:00:02.40,Default,,vanakkam, idhu oru mock caption\n",
      ),
    },
    "cutlist.json": {
      type: "application/json",
      body: Buffer.from(
        JSON.stringify(
          { ranges: [{ start: 0, end: 12.4 }, { start: 13.1, end: 30.01 }], dropped: { silence: 5, retake: 2 } },
          null,
          2,
        ),
      ),
    },
    "words.json": {
      type: "application/json",
      body: Buffer.from(
        JSON.stringify(
          { words: [{ text: "vanakkam", start: 0.1, end: 0.7, conf: 1 }] },
          null,
          2,
        ),
      ),
    },
    "nle.zip": {
      type: "application/zip",
      body: Buffer.concat([Buffer.from([0x50, 0x4b, 0x03, 0x04]), Buffer.alloc(2048)]),
    },
  };
  const entry = allowed[name];
  if (!entry) {
    sendError(res, 404, "unknown file name");
    return;
  }
  const headers = {
    "Content-Type": entry.type,
    "Content-Length": entry.body.length,
    ...CORS,
  };
  if (download) headers["Content-Disposition"] = `attachment; filename="${name}"`;
  res.writeHead(200, headers);
  res.end(entry.body);
}

const server = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS);
    res.end();
    return;
  }

  const url = new URL(req.url || "/", `http://localhost:${PORT}`);
  const path = url.pathname.replace(/\/+$/, "") || "/";

  try {
    if (req.method === "GET" && path === "/health") {
      sendJson(res, 200, { ok: true, gemini_key: GEMINI_KEY, version: "0.1.0-mock" });
      return;
    }
    if (req.method === "GET" && path === "/presets") {
      sendJson(res, 200, { presets: PRESETS });
      return;
    }
    if (req.method === "POST" && path === "/uploads") {
      await handleUpload(req, res);
      return;
    }
    if (req.method === "POST" && path === "/jobs") {
      await handleJobCreate(req, res);
      return;
    }

    let match = path.match(/^\/jobs\/([^/]+)$/);
    if (req.method === "GET" && match) {
      const job = jobs.get(match[1]);
      if (!job) return sendError(res, 404, "unknown job id");
      sendJson(res, 200, snapshot(job));
      return;
    }

    match = path.match(/^\/jobs\/([^/]+)\/events$/);
    if (req.method === "GET" && match) {
      const job = jobs.get(match[1]);
      if (!job) return sendError(res, 404, "unknown job id");
      handleEvents(req, res, job);
      return;
    }

    match = path.match(/^\/jobs\/([^/]+)\/files\/([^/]+)$/);
    if (req.method === "GET" && match) {
      const job = jobs.get(match[1]);
      if (!job) return sendError(res, 404, "unknown job id");
      handleFile(res, job, decodeURIComponent(match[2]), url.searchParams.has("dl"));
      return;
    }

    sendError(res, 404, `no route for ${req.method} ${path}`);
  } catch (err) {
    sendError(res, 500, err instanceof Error ? err.message : "mock error");
  }
});

server.listen(PORT, () => {
  console.log(`mock sidecar on http://localhost:${PORT}`);
  console.log(`  gemini_key=${GEMINI_KEY}${FAIL_JOB ? " · forced failures on" : ""}`);
});
