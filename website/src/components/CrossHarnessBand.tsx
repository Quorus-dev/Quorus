import { useId, useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import HarnessFlow from "./illustrations/HarnessFlow";
import CodeBlock from "./CodeBlock";
import AsciinemaPlayer from "./AsciinemaPlayer";
import { CROSS_HARNESS_COPY, HARNESS_LABELS } from "../data/cross_harness_copy";

/**
 * CrossHarnessBand — the cross-vendor compatibility section.
 *
 * Quorus's defensible moat: works across Claude Code, Cursor, Gemini CLI,
 * and Codex CLI without code changes on the user side. This band leads
 * with that claim, illustrates it via HarnessFlow (4 vendors → 1 relay) +
 * an asciinema demo recording, and proves it with a tabbed install
 * snippet for each harness.
 *
 * Surface theme: dark (data-theme="dark", --color-ink background).
 * Self-contained: no props, no external state.
 *
 * Visible strings (headline / subline / CTA) are sourced from
 * `src/data/cross_harness_copy.ts` so the test suite can assert verbatim
 * matches and so doc edits travel with code edits.
 */

const EASE = [0.16, 1, 0.3, 1] as const;

const NOISE_SVG =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'>
      <filter id='n'>
        <feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/>
        <feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.06 0'/>
      </filter>
      <rect width='100%' height='100%' filter='url(#n)'/>
    </svg>`,
  );

type TabId = "claude" | "codex" | "gemini" | "cursor" | "opencode" | "cline";

interface TabSpec {
  id: TabId;
  label: string;
  filename: string;
  lang: string;
  prompt: string;
  command: string;
  /** Short caption rendered below the snippet to set context. */
  note: string;
}

// Snippets are mirrored from /docs/CROSS_HARNESS_NOTIFICATIONS.md so any
// future doc edit must propagate here. Keeping them inline (instead of
// fetching) means the section stays static-renderable and SSR-safe.
const TABS: readonly TabSpec[] = [
  {
    id: "claude",
    label: "Claude Code",
    filename: "terminal",
    lang: "bash",
    prompt: "$",
    command: "quorus hook enable",
    note: "Adds a UserPromptSubmit hook to ~/.claude/settings.json. Restart Claude Code once after enabling.",
  },
  {
    id: "cursor",
    label: "Cursor",
    filename: "~/.cursor/hooks.json",
    lang: "json",
    prompt: "",
    command: `{
  "version": 1,
  "hooks": {
    "sessionStart": [{ "command": "quorus hook cursor-session" }],
    "stop": [{ "command": "quorus hook cursor-stop", "loop_limit": 3 }]
  }
}`,
    note: "sessionStart injects unread messages on boot; stop catches new messages mid-session as auto-followups.",
  },
  {
    id: "gemini",
    label: "Gemini CLI",
    filename: "~/.gemini/settings.json",
    lang: "json",
    prompt: "",
    command: `{
  "hooks": {
    "BeforeAgent": [
      {
        "hooks": [
          { "type": "command", "command": "quorus hook gemini-beforeagent" }
        ]
      }
    ]
  },
  "mcpServers": {
    "quorus": {
      "command": "quorus-mcp",
      "env": {
        "RELAY_URL": "https://quorus-relay.fly.dev",
        "INSTANCE_NAME": "<your-name>-gemini"
      }
    }
  }
}`,
    note: "BeforeAgent appends Quorus context to every turn — the cleanest integration surface across all harnesses.",
  },
  {
    id: "codex",
    label: "Codex CLI",
    filename: "terminal",
    lang: "bash",
    prompt: "$",
    command: "quorus codex-agent --room <room> --autonomous",
    note: "Codex doesn't expose per-tool hooks, so the codex-agent loop wraps each invocation. Nothing to install in settings.",
  },
  {
    id: "opencode",
    label: "Opencode",
    filename: "terminal",
    lang: "bash",
    prompt: "$",
    command: "quorus connect opencode --name <your-name>-opencode",
    note: "Tier-A: reflexd wakes Opencode via `opencode run` on @-mention. Auth via opencode auth login (75+ providers).",
  },
  {
    id: "cline",
    label: "Cline",
    filename: "terminal",
    lang: "bash",
    prompt: "$",
    command:
      "npm install -g cline && quorus connect cline --name <your-name>-cline",
    note: "Tier-A: reflexd wakes Cline via the standalone `cline` CLI on @-mention. macOS/Linux only (preview).",
  },
] as const;

function findTab(id: TabId): TabSpec {
  // Tabs are a closed set; this is exhaustive by construction.
  const t = TABS.find((tab) => tab.id === id);
  if (!t) throw new Error(`Unknown tab: ${id}`);
  return t;
}

export default function CrossHarnessBand(): JSX.Element {
  const prefersReduced = useReducedMotion();
  const [active, setActive] = useState<TabId>("claude");
  const indicatorLayoutId = useId();
  const tab = useMemo(() => findTab(active), [active]);

  return (
    <section
      data-theme="dark"
      aria-labelledby="cross-harness-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: "var(--color-ink)" }}
    >
      {/* Off-center accent radial — matches Control Center / CTA family */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 70% 50% at 50% 30%, rgba(94,179,168,0.10), transparent 70%)",
        }}
      />
      {/* 1px noise grain */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage: `url("${NOISE_SVG}")`,
          backgroundSize: "200px 200px",
          mixBlendMode: "overlay",
        }}
      />

      <div className="relative mx-auto max-w-7xl px-6 py-24 md:py-32">
        {/* Header */}
        <div className="mx-auto max-w-3xl text-center">
          <motion.p
            initial={prefersReduced ? false : { opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.3 }}
            transition={{ duration: 0.6, ease: EASE }}
            className="text-[11px] uppercase"
            style={{
              color: "var(--color-accent-on-ink)",
              fontFamily: "var(--font-mono)",
              letterSpacing: "0.22em",
            }}
          >
            {CROSS_HARNESS_COPY.eyebrow}
          </motion.p>
          <motion.h2
            id="cross-harness-heading"
            initial={prefersReduced ? false : { opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.3 }}
            transition={{ duration: 0.6, ease: EASE, delay: 0.05 }}
            className="mt-4 text-balance"
            style={{
              color: "var(--color-text-on-ink)",
              fontFamily: "var(--font-sans)",
              fontSize: "clamp(36px, 4.6vw, 60px)",
              fontWeight: 600,
              lineHeight: 1.05,
              letterSpacing: "-0.02em",
            }}
          >
            {CROSS_HARNESS_COPY.headline}
          </motion.h2>
          <motion.p
            initial={prefersReduced ? false : { opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.3 }}
            transition={{ duration: 0.6, ease: EASE, delay: 0.1 }}
            className="mx-auto mt-5 max-w-2xl text-pretty"
            style={{
              color: "var(--color-text-on-ink-secondary)",
              fontFamily: "var(--font-sans)",
              fontSize: 17,
              lineHeight: 1.6,
            }}
          >
            {CROSS_HARNESS_COPY.subline}
          </motion.p>
        </div>

        {/* Flow diagram */}
        <motion.div
          initial={prefersReduced ? false : { opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.2 }}
          transition={{ duration: 0.7, ease: EASE, delay: 0.15 }}
          className="mx-auto mt-16 w-full max-w-5xl"
        >
          <HarnessFlow />
          <p className="sr-only" aria-label="Supported coding agents">
            Supported coding agents: {HARNESS_LABELS.join(", ")}.
          </p>
        </motion.div>

        {/* Demo terminal — lazy-loaded asciinema */}
        <motion.div
          initial={prefersReduced ? false : { opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.15 }}
          transition={{ duration: 0.7, ease: EASE, delay: 0.18 }}
          className="mx-auto mt-12 w-full max-w-4xl"
        >
          <AsciinemaPlayer
            castUrl="/casts/demo_reflex.cast"
            autoPlay
            loop
            idleTimeLimit={1.5}
            caption="quorus init — one command, four agents, zero cloud."
          />
        </motion.div>

        {/* Tab switcher */}
        <motion.div
          initial={prefersReduced ? false : { opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.2 }}
          transition={{ duration: 0.6, ease: EASE, delay: 0.2 }}
          className="mx-auto mt-16 w-full max-w-3xl"
        >
          <div
            role="tablist"
            aria-label="Install Quorus in your harness"
            className="relative flex flex-wrap items-center justify-center gap-1 border-b"
            style={{ borderColor: "var(--color-border-dark)" }}
          >
            {TABS.map((t) => {
              const isActive = t.id === active;
              return (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  id={`tab-${t.id}`}
                  aria-selected={isActive}
                  aria-controls={`panel-${t.id}`}
                  tabIndex={isActive ? 0 : -1}
                  onClick={() => setActive(t.id)}
                  className="relative px-4 py-3 text-[13px] transition-colors duration-200"
                  style={{
                    color: isActive
                      ? "var(--color-text-on-ink)"
                      : "var(--color-text-on-ink-muted)",
                    fontFamily: "var(--font-sans)",
                    fontWeight: 500,
                    letterSpacing: "-0.005em",
                  }}
                  onMouseEnter={(e) => {
                    if (!isActive) {
                      e.currentTarget.style.color =
                        "var(--color-text-on-ink-secondary)";
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!isActive) {
                      e.currentTarget.style.color =
                        "var(--color-text-on-ink-muted)";
                    }
                  }}
                >
                  {t.label}
                  {isActive ? (
                    <motion.span
                      layoutId={indicatorLayoutId}
                      className="absolute inset-x-2 -bottom-px h-[2px]"
                      style={{
                        backgroundColor: "var(--color-accent-on-ink)",
                      }}
                      transition={{
                        type: "spring",
                        stiffness: 380,
                        damping: 30,
                      }}
                    />
                  ) : null}
                </button>
              );
            })}
          </div>

          <div
            role="tabpanel"
            id={`panel-${tab.id}`}
            aria-labelledby={`tab-${tab.id}`}
            className="mt-6"
          >
            <CodeBlock
              command={tab.command}
              filename={tab.filename}
              lang={tab.lang}
              prompt={tab.prompt}
            />
            <p
              className="mt-1 text-center text-[12px]"
              style={{
                color: "var(--color-text-on-ink-muted)",
                fontFamily: "var(--font-sans)",
                lineHeight: 1.5,
              }}
            >
              {tab.note}
            </p>
          </div>

          {/* CTA — mono accent link, not a button */}
          <div className="mt-8 flex items-center justify-center">
            <a
              href={CROSS_HARNESS_COPY.ctaHref}
              data-testid="cross-harness-cta"
              className="inline-flex items-center gap-2 rounded-md px-3 py-2 text-[13px] transition-colors focus-visible:outline-2 focus-visible:outline-offset-2"
              style={{
                color: "var(--color-accent-on-ink)",
                fontFamily: "var(--font-mono)",
                letterSpacing: "0.04em",
                borderBottom: "1px solid rgba(94,179,168,0.4)",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--color-text-on-ink)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--color-accent-on-ink)";
              }}
            >
              {CROSS_HARNESS_COPY.ctaLabel}
            </a>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
