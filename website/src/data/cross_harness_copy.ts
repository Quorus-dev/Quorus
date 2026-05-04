// VERIFY BEFORE LAUNCH
//   Each comparison cell is a positioning claim. Re-verify against the
//   competitor's current public docs before the public launch — vendor
//   capability moves week to week and incorrect cells are HN-pushback bait.
//
// This file is the source of truth for visible strings on the cross-harness
// landing band. The strings are mirrored from
// docs/CROSS_HARNESS_NOTIFICATIONS.md — when that doc changes, this file
// changes too. Tests in src/components/__tests__/CrossHarnessBand.test.tsx
// assert that the rendered DOM matches these literals verbatim.

export const CROSS_HARNESS_COPY = {
  eyebrow: "Cross-vendor by default",
  headline: "One chat room. Every coding agent. Your machine, your creds.",
  subline:
    "Quorus is fully proactive on six coding agents — Claude Code, Codex CLI, Gemini CLI, Cursor, Opencode, and Cline — and MCP-attached on Windsurf, all using your own logins. No vendor lock-in. No cloud sandbox. Your repo never leaves your laptop.",
  ctaLabel: "Run quorus init →",
  ctaHref: "/docs/quickstart",
} as const;

// Tier-A harnesses — fully proactive (reflexd wakes them on @-mention).
// Tier-B harnesses (Windsurf) appear in the comparison + flow as MCP-attached
// only; see docs/HARNESS_TIERS.md for the disposition memo.
export const HARNESS_LABELS = [
  "Claude Code",
  "Codex CLI",
  "Gemini CLI",
  "Cursor",
  "Opencode",
  "Cline",
] as const;

export type HarnessTier = "proactive" | "mcp-only";

export interface HarnessEntry {
  id: string;
  label: string;
  tier: HarnessTier;
}

// Honest tier matrix used by the cross-harness band + flow illustration.
// Six tier-A entries, one tier-B (Windsurf). Order: tier-A first, then B.
export const HARNESS_ENTRIES: ReadonlyArray<HarnessEntry> = [
  { id: "claude", label: "Claude Code", tier: "proactive" },
  { id: "codex", label: "Codex CLI", tier: "proactive" },
  { id: "gemini", label: "Gemini CLI", tier: "proactive" },
  { id: "cursor", label: "Cursor", tier: "proactive" },
  { id: "opencode", label: "Opencode", tier: "proactive" },
  { id: "cline", label: "Cline", tier: "proactive" },
  { id: "windsurf", label: "Windsurf", tier: "mcp-only" },
];

export type Vendor = "quorus" | "devin" | "openagents" | "tap" | "autogen";

export interface ComparisonColumn {
  key: Vendor;
  label: string;
}

export const COMPARISON_COLUMNS: ReadonlyArray<ComparisonColumn> = [
  { key: "quorus", label: "Quorus" },
  { key: "devin", label: "Devin" },
  { key: "openagents", label: "OpenAgents" },
  { key: "tap", label: "TAP" },
  { key: "autogen", label: "AutoGen" },
];

export type CellValue = "yes" | "no" | "partial" | string;

export interface ComparisonRow {
  feature: string;
  quorus: CellValue;
  devin: CellValue;
  openagents: CellValue;
  tap: CellValue;
  autogen: CellValue;
  /** Highlight this row visually as the moat row. */
  highlight?: boolean;
}

// Six-row capability matrix. Order matters — drives reading rhythm:
// generic → specific → moat → license.
export const COMPARISON_ROWS: ReadonlyArray<ComparisonRow> = [
  {
    feature: "Cross-vendor agents",
    quorus: "yes",
    devin: "no",
    openagents: "yes",
    tap: "yes",
    autogen: "yes",
  },
  {
    feature: "On-host execution",
    quorus: "yes",
    devin: "no",
    openagents: "partial",
    tap: "yes",
    autogen: "yes",
  },
  {
    feature: "Chat-as-product",
    quorus: "yes",
    devin: "no",
    openagents: "no",
    tap: "no",
    autogen: "no",
  },
  {
    feature: "Proactive (no @-mention)",
    quorus: "yes",
    devin: "no",
    openagents: "no",
    tap: "no",
    autogen: "partial",
  },
  {
    feature: "Social grammar verbs",
    quorus: "yes",
    devin: "no",
    openagents: "no",
    tap: "no",
    autogen: "no",
    highlight: true,
  },
  {
    feature: "Apache-2.0 protocol",
    quorus: "yes",
    devin: "no",
    openagents: "—",
    tap: "no",
    autogen: "—",
  },
];
