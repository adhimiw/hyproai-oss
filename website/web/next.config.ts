import type { NextConfig } from "next";

/**
 * The browser only ever talks to `/api/sidecar/*`; this rewrite proxies to the
 * sidecar (website/API_CONTRACT.md). Escape hatch: set
 * NEXT_PUBLIC_SIDECAR_ORIGIN and the client (lib/api.ts) calls that origin
 * directly instead — useful if SSE through the rewrite misbehaves.
 *
 * Large uploads (≤500 MB) can't go through the middleware rewrite: Next
 * truncates rewrites' request bodies at 10 MB (middlewareClientMaxBodySize
 * tops out at 2 MB), which cut the multipart stream mid-flight and surfaced
 * as a sidecar 500 (ClientDisconnect on /uploads). So `/uploads` is served
 * by a streaming Route Handler (app/api/sidecar/uploads/route.ts) instead;
 * every other `/api/sidecar/*` path keeps the cheap rewrite.
 */
const SIDECAR_URL = process.env.SIDECAR_URL || "http://localhost:8787";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        // Everything EXCEPT /uploads — that one route handler handles.
        source: "/api/sidecar/:path((?!uploads$).*)",
        destination: `${SIDECAR_URL}/:path`,
      },
    ];
  },
};

export default nextConfig;
