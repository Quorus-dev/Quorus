import { useState } from "react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import CodeBlock from "./CodeBlock";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * QuickstartBand — cream surface, four-tab code presenter showing the
 * three commands needed to install Quorus, initialize a room, register
 * the MCP server, and send a message. Active tab uses a Framer Motion
 * `layoutId` underline so switching tabs slides smoothly.
 *
 * The CodeBlock component owns its dark surface — this band stays cream.
 *
 * Self-contained — no props.
 */

interface Tab {
  id: string;
  label: string;
  command: string;
  lang: string;
  filename?: string;
}

const TABS: Tab[] = [
  {
    id: "install",
    label: "Install",
    command:
      'pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"',
    lang: "bash",
  },
  {
    id: "init",
    label: "Init",
    command: "quorus init\nquorus join my-room",
    lang: "bash",
  },
  {
    id: "mcp",
    label: "MCP",
    command: "claude mcp add quorus -- quorus-mcp",
    lang: "bash",
  },
  {
    id: "send",
    label: "Send",
    command: 'quorus say my-room "starting work on auth.py"',
    lang: "bash",
  },
];

export default function QuickstartBand(): JSX.Element {
  const [activeId, setActiveId] = useState<string>(TABS[0].id);
  const active = TABS.find((t) => t.id === activeId) ?? TABS[0];

  return (
    <section
      aria-labelledby="quickstart-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: "var(--color-cream)" }}
    >
      {/* Top hairline */}
      <hr className="hairline" />

      <div className="relative mx-auto max-w-7xl px-6 py-24 lg:py-32">
        {/* Eyebrow */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.5, ease: EASE }}
          className="eyebrow"
          style={{ color: "var(--color-accent)" }}
        >
          Quickstart
        </motion.div>

        {/* Heading */}
        <motion.h2
          id="quickstart-heading"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.65, delay: 0.05, ease: EASE }}
          className="mt-3 max-w-3xl"
          style={{
            color: "var(--color-text-on-cream)",
            fontWeight: 600,
            letterSpacing: "-0.022em",
            lineHeight: 1.05,
            fontSize: "clamp(36px, 4.6vw, 60px)",
          }}
        >
          Three commands. Any agent. Any model.
        </motion.h2>

        {/* Subhead */}
        <motion.p
          initial={{ opacity: 0, y: 14 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.6, delay: 0.12, ease: EASE }}
          className="mt-5 max-w-2xl text-[18px] leading-[1.55]"
          style={{ color: "var(--color-text-on-cream-secondary)" }}
        >
          pipx install. quorus init. Done. Drop the MCP server into Claude Code,
          Cursor, or whatever you use today.
        </motion.p>

        {/* Tabbed code block */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.3 }}
          transition={{ duration: 0.7, delay: 0.18, ease: EASE }}
          className="mt-12 max-w-3xl"
        >
          {/* Tab strip */}
          <div
            role="tablist"
            aria-label="Quickstart commands"
            className="flex items-center gap-1 border-b"
            style={{ borderColor: "var(--color-border-light)" }}
          >
            {TABS.map((tab) => {
              const isActive = tab.id === activeId;
              return (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  id={`quickstart-tab-${tab.id}`}
                  aria-selected={isActive}
                  aria-controls={`quickstart-panel-${tab.id}`}
                  tabIndex={isActive ? 0 : -1}
                  onClick={() => setActiveId(tab.id)}
                  className="relative px-4 py-3 font-mono text-[12px] tracking-[0.12em] uppercase transition-colors"
                  style={{
                    color: isActive
                      ? "var(--color-text-on-cream)"
                      : "var(--color-text-on-cream-muted)",
                  }}
                  onMouseEnter={(e) => {
                    if (!isActive)
                      e.currentTarget.style.color =
                        "var(--color-text-on-cream-secondary)";
                  }}
                  onMouseLeave={(e) => {
                    if (!isActive)
                      e.currentTarget.style.color =
                        "var(--color-text-on-cream-muted)";
                  }}
                >
                  {tab.label}
                  {isActive ? (
                    <motion.span
                      layoutId="quickstart-tab-underline"
                      className="absolute inset-x-2 -bottom-px block h-[2px]"
                      style={{ backgroundColor: "var(--color-accent)" }}
                      transition={{
                        type: "spring",
                        stiffness: 380,
                        damping: 32,
                      }}
                    />
                  ) : null}
                </button>
              );
            })}
          </div>

          {/* Tab panel — single CodeBlock, key changes on tab switch so the
              copy state resets cleanly between commands. */}
          <div
            role="tabpanel"
            id={`quickstart-panel-${active.id}`}
            aria-labelledby={`quickstart-tab-${active.id}`}
          >
            <CodeBlock
              key={active.id}
              command={active.command}
              lang={active.lang}
              filename={active.filename}
              prompt={active.command.includes("\n") ? "" : "$"}
            />
          </div>
        </motion.div>

        {/* CTA link below */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.3 }}
          transition={{ duration: 0.6, delay: 0.28, ease: EASE }}
          className="mt-8 max-w-3xl"
        >
          <Link
            to="/console"
            className="group inline-flex items-center gap-2 font-mono text-[13px] transition-colors"
            style={{ color: "var(--color-accent)" }}
          >
            <span>Open the live console</span>
            <span
              aria-hidden
              className="inline-block transition-transform duration-200 group-hover:translate-x-0.5"
            >
              →
            </span>
          </Link>
        </motion.div>
      </div>

      {/* Bottom hairline */}
      <hr className="hairline" />
    </section>
  );
}
