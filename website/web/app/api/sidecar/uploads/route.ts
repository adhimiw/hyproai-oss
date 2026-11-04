/**
 * Streaming proxy for the single large-body endpoint that can't go through
 * the middleware rewrite: POST /api/sidecar/uploads (≤500 MB, contract
 * website/API_CONTRACT.md).
 *
 * Next truncates rewritten request bodies at 10 MB
 * (middlewareClientMaxBodySize caps at 2 MB), which cut the multipart
 * stream mid-flight and surfaced as a sidecar 500 (ClientDisconnect on
 * /uploads — see next.config.ts). Route Handlers stream request bodies
 * without that cap (serverActions.bodySizeLimit allows 500 MB), so this
 * handler forwards the upload body as a stream instead of buffering it.
 *
 * Everything else under /api/sidecar/* still uses the cheap rewrite.
 */
import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// streaming route handlers cap bodies at serverActions.bodySizeLimit
export const bodySizeLimit = "500mb";

const SIDECAR_URL = process.env.SIDECAR_URL || "http://localhost:8787";

export async function POST(request: NextRequest): Promise<Response> {
  const target = `${SIDECAR_URL}/uploads`;

  // Forward the incoming request body as a stream (no full buffering).
  const body = (request as unknown as { body?: ReadableStream | null }).body;
  const contentLength = request.headers.get("content-length");

  const headers: Record<string, string> = {};
  const contentType = request.headers.get("content-type");
  if (contentType) headers["content-type"] = contentType;
  if (contentLength) headers["content-length"] = contentLength;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: "POST",
      headers,
      body: body ?? undefined,
      duplex: "half",
    } as RequestInit);
  } catch {
    return Response.json(
      { error: "can't reach the sidecar (502)" },
      { status: 502 },
    );
  }

  // Stream the sidecar's response back (usually a small JSON record).
  const responseBody = upstream.body as unknown as ReadableStream | null;
  const responseHeaders: Record<string, string> = {};
  const responseType = upstream.headers.get("content-type");
  if (responseType) responseHeaders["content-type"] = responseType;

  return new Response(responseBody, {
    status: upstream.status,
    headers: responseHeaders,
  });
}
