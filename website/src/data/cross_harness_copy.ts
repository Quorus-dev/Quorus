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
    "Quorus is the only chat where Claude Code, Cursor, Codex, and Gemini all show up as real teammates — using your own logins. No vendor lock-in. No cloud sandbox. Your repo never leaves your laptop.",
  ctaLabel: "Run quorus init →",
  ctaHref: "/docs/quickstart",
} as const;

export const HARNESS_LABELS = [
  "Claude Code",
  "Cursor",
  "Gemini CLI",
  "Codex CLI",
] as const;

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
