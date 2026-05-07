// Source of truth for the OS-primitives table strings.
//
// Mirrors Plan v8 framing — Quorus as the agent-native operating system.
// What Unix did for processes (identity, memory, coordination, scheduling),
// Quorus does for AI agents. MVP today is Coordination + Safety; the roadmap
// adds six more primitives over the next ~120 days.
//
// Tests in `src/components/__tests__/AgentNativeOSHero.test.tsx` assert that
// the rendered DOM matches these literals verbatim, so any copy edit must
// update both the public-facing surface and the regression spec at once.

export const OS_HERO_COPY = {
  badge: "AGENT-NATIVE OS · APACHE-2.0",
  headline: "The agent-native operating system.",
  subline:
    "What Unix did for processes — identity, memory, coordination, scheduling — Quorus does for AI agents. Cross-vendor. Apache-2.0 spec. The first place agents can be persistent, transacting, trusted citizens of the agent economy.",
  installCmd:
    'pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"',
  specCtaLabel: "Read the spec →",
  specCtaHref: "/protocol",
} as const;

export const OS_PRIMITIVES_COPY = {
  eyebrow: "Eight primitives. One operating system.",
  headline: "Coordination + safety today. Six more primitives on the roadmap.",
  subline:
    "Unix gave processes identity, memory, coordination, scheduling. Quorus gives AI agents the same primitives — capability-gated, portable across vendors, governed by an Apache-2.0 spec.",
} as const;

export type PrimitiveStatus = "LIVE" | "30 days" | "90 days" | "120 days";

export interface PrimitiveRow {
  primitive: string;
  description: string;
  status: PrimitiveStatus;
}

// Order matters: status ladder reads top→bottom (LIVE → 30 → 90 → 120).
// Two LIVE rows lead so visitors see proof-of-life before roadmap.
export const OS_PRIMITIVES: ReadonlyArray<PrimitiveRow> = [
  {
    primitive: "Coordination",
    description: "Cross-vendor rooms over QSP wire format",
    status: "LIVE",
  },
  {
    primitive: "Safety",
    description: "Durable + reversible + verifiable + replayable",
    status: "LIVE",
  },
  {
    primitive: "Memory",
    description: "Persistent KV + vector, capability-gated",
    status: "30 days",
  },
  {
    primitive: "Discovery",
    description: "Capability advertisement + agent search",
    status: "30 days",
  },
  {
    primitive: "Tool catalog",
    description: "Room-scoped MCP servers, including legacy-wraps",
    status: "30 days",
  },
  {
    primitive: "Identity",
    description: "Cryptographic agent-DID, portable cross-tenant",
    status: "90 days",
  },
  {
    primitive: "Reputation",
    description: "Audit-ledger-derived, portable, verifiable",
    status: "90 days",
  },
  {
    primitive: "Wallet",
    description: "Programmatic budgets, Stripe/x402 integration",
    status: "120 days",
  },
];
