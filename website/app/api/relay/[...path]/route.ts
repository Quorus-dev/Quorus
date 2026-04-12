/**
 * Relay proxy — forwards browser requests to any Murmur relay, avoiding CORS.
 *
 * The browser passes:
 *   x-relay-url: https://your-relay.railway.app
 *   x-relay-key: <relay secret or API key>
 *
 * This handler strips those headers, builds the real upstream URL, and
 * proxies the request with Authorization: Bearer <key>.
 *
 * SSRF protection: blocks cloud metadata endpoints.
 */

import { NextRequest, NextResponse } from "next/server";

// Cloud metadata endpoints — always blocked
const BLOCKED_HOSTS = new Set([
  "169.254.169.254",
  "metadata.google.internal",
  "metadata.internal",
  "100.100.100.200",
]);

function buildTarget(
  relayUrl: string,
  segments: string[],
  search: string,
): URL | null {
  try {
    const base = relayUrl.replace(/\/+$/, "");
    const url = new URL(`${base}/${segments.join("/")}${search}`);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    if (BLOCKED_HOSTS.has(url.hostname)) return null;
    return url;
  } catch {
    return null;
  }
}

async function proxy(
  req: NextRequest,
  segments: string[],
): Promise<NextResponse> {
  const relayUrl = req.headers.get("x-relay-url");
  if (!relayUrl) {
    return NextResponse.json({ error: "Missing x-relay-url" }, { status: 400 });
  }

  const target = buildTarget(relayUrl, segments, req.nextUrl.search);
  if (!target) {
    return NextResponse.json(
      { error: "Invalid or blocked relay URL" },
      { status: 400 },
    );
  }

  const apiKey = req.headers.get("x-relay-key");
  const upstreamHeaders: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (apiKey) upstreamHeaders["Authorization"] = `Bearer ${apiKey}`;

  let body: string | undefined;
  if (req.method !== "GET" && req.method !== "HEAD") {
    body = await req.text().catch(() => undefined);
  }

  try {
    const resp = await fetch(target.toString(), {
      method: req.method,
      headers: upstreamHeaders,
      body,
      signal: AbortSignal.timeout(12_000),
    });

    const text = await resp.text();
    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }

    return NextResponse.json(data, { status: resp.status });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Relay unreachable";
    console.error(`[relay-proxy] ${req.method} ${target} — ${msg}`);
    return NextResponse.json({ error: msg }, { status: 502 });
  }
}

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: RouteContext) {
  return proxy(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: RouteContext) {
  return proxy(req, (await ctx.params).path);
}
export async function PUT(req: NextRequest, ctx: RouteContext) {
  return proxy(req, (await ctx.params).path);
}
export async function PATCH(req: NextRequest, ctx: RouteContext) {
  return proxy(req, (await ctx.params).path);
}
export async function DELETE(req: NextRequest, ctx: RouteContext) {
  return proxy(req, (await ctx.params).path);
}
