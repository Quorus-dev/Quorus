/**
 * Relay proxy — forwards browser requests to Murmur relays, avoiding CORS.
 *
 * The browser passes:
 *   x-relay-url: https://your-relay.railway.app
 *   x-relay-key: <relay secret or API key>
 *
 * This handler strips those headers, builds the real upstream URL, and
 * proxies the request with Authorization: Bearer <key>.
 *
 * SSRF protection:
 *   - Blocks cloud metadata endpoints
 *   - Blocks private/reserved IP ranges (RFC1918, loopback, link-local)
 *   - Blocks localhost variants
 *   - DNS rebinding protection: resolves hostname and verifies all IPs are public
 *   - PRODUCTION: Requires RELAY_ALLOWLIST (fail closed)
 *   - DEVELOPMENT: Allows any public hostname if RELAY_ALLOWLIST is unset
 */

import { NextRequest, NextResponse } from "next/server";
import dns from "dns/promises";

// Cloud metadata hostnames — always blocked
const BLOCKED_HOSTS = new Set([
  "metadata.google.internal",
  "metadata.internal",
  "metadata.aws.internal",
  "instance-data",
  "localhost",
  "localhost.localdomain",
]);

// Production check — fail closed if allowlist not configured
const IS_PRODUCTION = process.env.NODE_ENV === "production";

// Relay allowlist from environment (comma-separated hostnames)
// REQUIRED in production; optional in development
const RELAY_ALLOWLIST: Set<string> | null = (() => {
  const envVal = process.env.RELAY_ALLOWLIST;
  if (!envVal) {
    if (IS_PRODUCTION) {
      console.error(
        "[relay-proxy] FATAL: RELAY_ALLOWLIST must be set in production. " +
        "Set it to a comma-separated list of allowed relay hostnames."
      );
    }
    return null;
  }
  return new Set(envVal.split(",").map((h) => h.trim().toLowerCase()));
})();

/**
 * Check if an IP address is in a private/reserved range.
 * Blocks: loopback, private (RFC1918), link-local, multicast, etc.
 */
function isPrivateOrReservedIP(ip: string): boolean {
  // IPv4 checks
  const ipv4Match = ip.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (ipv4Match) {
    const [, a, b, c, d] = ipv4Match.map(Number);
    // Validate octets
    if ([a, b, c, d].some((n) => n > 255)) return true;

    // Loopback: 127.0.0.0/8
    if (a === 127) return true;
    // Private: 10.0.0.0/8
    if (a === 10) return true;
    // Private: 172.16.0.0/12
    if (a === 172 && b >= 16 && b <= 31) return true;
    // Private: 192.168.0.0/16
    if (a === 192 && b === 168) return true;
    // Link-local: 169.254.0.0/16 (includes cloud metadata 169.254.169.254)
    if (a === 169 && b === 254) return true;
    // Multicast: 224.0.0.0/4
    if (a >= 224 && a <= 239) return true;
    // Reserved: 240.0.0.0/4
    if (a >= 240) return true;
    // This host: 0.0.0.0/8
    if (a === 0) return true;
    // Shared address space: 100.64.0.0/10 (CGNAT, includes 100.100.100.200)
    if (a === 100 && b >= 64 && b <= 127) return true;
    // Documentation: 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24
    if (a === 192 && b === 0 && c === 2) return true;
    if (a === 198 && b === 51 && c === 100) return true;
    if (a === 203 && b === 0 && c === 113) return true;
  }

  // IPv6 checks
  const ipLower = ip.toLowerCase();
  // Loopback: ::1
  if (ipLower === "::1" || ipLower === "0:0:0:0:0:0:0:1") return true;
  // Unspecified: ::
  if (ipLower === "::" || ipLower === "0:0:0:0:0:0:0:0") return true;
  // Link-local: fe80::/10
  if (ipLower.startsWith("fe8") || ipLower.startsWith("fe9") ||
      ipLower.startsWith("fea") || ipLower.startsWith("feb")) return true;
  // Unique local: fc00::/7 (fd00::/8 commonly used)
  if (ipLower.startsWith("fc") || ipLower.startsWith("fd")) return true;
  // IPv4-mapped IPv6: ::ffff:x.x.x.x
  const v4mappedMatch = ipLower.match(/^::ffff:(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$/);
  if (v4mappedMatch) {
    return isPrivateOrReservedIP(v4mappedMatch[1]);
  }

  return false;
}

/**
 * Check if a hostname looks like an IP address (numeric or bracket notation).
 */
function extractIPFromHostname(hostname: string): string | null {
  // Plain IPv4
  if (/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(hostname)) {
    return hostname;
  }
  // Bracketed IPv6: [::1]
  const bracketMatch = hostname.match(/^\[(.+)\]$/);
  if (bracketMatch) return bracketMatch[1];
  // Plain IPv6 (uncommon in URLs but check anyway)
  if (hostname.includes(":")) return hostname;
  return null;
}

/**
 * Resolve hostname to IP addresses and check if any are private/reserved.
 * Returns true if safe (all resolved IPs are public), false otherwise.
 */
async function isDNSSafe(hostname: string): Promise<boolean> {
  // If it's already an IP, we checked it in buildTarget
  if (extractIPFromHostname(hostname)) return true;

  try {
    // Resolve both IPv4 and IPv6
    const [ipv4Addrs, ipv6Addrs] = await Promise.all([
      dns.resolve4(hostname).catch(() => [] as string[]),
      dns.resolve6(hostname).catch(() => [] as string[]),
    ]);

    const allAddrs = [...ipv4Addrs, ...ipv6Addrs];

    // If no addresses resolved, block (fail closed)
    if (allAddrs.length === 0) {
      console.warn(`[relay-proxy] DNS resolution failed for ${hostname}`);
      return false;
    }

    // Check all resolved addresses
    for (const ip of allAddrs) {
      if (isPrivateOrReservedIP(ip)) {
        console.warn(
          `[relay-proxy] DNS rebinding blocked: ${hostname} resolved to private IP ${ip}`
        );
        return false;
      }
    }

    return true;
  } catch (err) {
    console.warn(`[relay-proxy] DNS lookup error for ${hostname}: ${err}`);
    return false;
  }
}

function buildTarget(
  relayUrl: string,
  segments: string[],
  search: string,
): URL | null {
  try {
    const base = relayUrl.replace(/\/+$/, "");
    const url = new URL(`${base}/${segments.join("/")}${search}`);

    // Protocol check
    if (!["http:", "https:"].includes(url.protocol)) return null;

    const hostLower = url.hostname.toLowerCase();

    // Blocked hostname check
    if (BLOCKED_HOSTS.has(hostLower)) return null;

    // Allowlist check (if configured)
    if (RELAY_ALLOWLIST && !RELAY_ALLOWLIST.has(hostLower)) {
      console.warn(`[relay-proxy] Blocked: ${hostLower} not in allowlist`);
      return null;
    }

    // IP address check — block private/reserved ranges
    const maybeIP = extractIPFromHostname(url.hostname);
    if (maybeIP && isPrivateOrReservedIP(maybeIP)) {
      console.warn(`[relay-proxy] Blocked private/reserved IP: ${maybeIP}`);
      return null;
    }

    return url;
  } catch {
    return null;
  }
}

async function proxy(
  req: NextRequest,
  segments: string[],
): Promise<NextResponse> {
  // Fail closed in production if allowlist not configured
  if (IS_PRODUCTION && !RELAY_ALLOWLIST) {
    return NextResponse.json(
      { error: "Relay proxy disabled: RELAY_ALLOWLIST not configured" },
      { status: 503 },
    );
  }

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

  // DNS rebinding protection: resolve hostname and verify IPs are public
  const dnsSafe = await isDNSSafe(target.hostname);
  if (!dnsSafe) {
    return NextResponse.json(
      { error: "Relay hostname resolves to private/reserved IP" },
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
