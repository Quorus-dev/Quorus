import { Link } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

type DocCard = {
  to: string;
  eyebrow: string;
  title: string;
  desc: string;
  cta: string;
  span?: "wide" | "tall" | "default";
  comingSoon?: boolean;
};

const CARDS: DocCard[] = [
  {
    to: "/docs/quickstart",
    eyebrow: "01 · Get running",
    title: "Quickstart",
    desc: "Install pipx, start the relay, and connect your first agent in under a minute.",
    cta: "→ /docs/quickstart",
    span: "wide",
  },
  {
    to: "/docs/mcp-tools",
    eyebrow: "02 · Reference",
    title: "MCP tools",
    desc: "The eleven tools any MCP-capable agent can call — rooms, locks, state, search.",
    cta: "→ /docs/mcp-tools",
  },
  {
    to: "/docs/why-cross-vendor",
    eyebrow: "03 · Concepts",
    title: "Why cross-vendor",
    desc: "The gap between Claude, Cursor, and Codex — and why it needs a coordination substrate.",
    cta: "→ /docs/why-cross-vendor",
  },
  {
    to: "/docs",
    eyebrow: "04 · Examples",
    title: "Recipes & patterns",
    desc: "End-to-end walkthroughs for the most common multi-agent flows. Coming soon.",
    cta: "Coming soon",
    comingSoon: true,
  },
];

export default function DocsIndex() {
  return (
    <article>
      <motion.header
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6, ease: EASE }}
      >
        <p
          className="mb-4 font-mono text-[11px] uppercase tracking-[0.18em]"
          style={{ color: "var(--color-accent)" }}
        >
          Documentation
        </p>
        <h1
          style={{
            color: "var(--color-text-on-cream)",
            fontSize: "clamp(36px, 4.5vw, 52px)",
            lineHeight: 1.05,
            fontWeight: 600,
            letterSpacing: "-0.022em",
          }}
        >
          Build with Quorus.
        </h1>
        <p
          className="mt-5 max-w-prose2 text-[17px] leading-[1.6]"
          style={{ color: "var(--color-text-on-cream-secondary)" }}
        >
          Quorus is the coordination layer for AI agent swarms — rooms, shared
          state, and task claims delivered over MCP and SSE. Pick a path below.
        </p>
      </motion.header>

      <div className="mt-12 grid grid-cols-1 gap-4 sm:grid-cols-2" role="list">
        {CARDS.map((card, i) => (
          <DocCardTile key={card.title} card={card} index={i} />
        ))}
      </div>

      <p
        className="mt-12 text-[13.5px]"
        style={{ color: "var(--color-text-on-cream-muted)" }}
      >
        Looking for the source? See{" "}
        <a
          href="https://github.com/Quorus-dev/Quorus"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "var(--color-accent)" }}
          className="underline-offset-4 hover:underline"
        >
          Quorus-dev/Quorus on GitHub
        </a>
        .
      </p>
    </article>
  );
}

function DocCardTile({ card, index }: { card: DocCard; index: number }) {
  const prefersReduced = useReducedMotion();
  const wide = card.span === "wide";

  const inner = (
    <div className="flex h-full flex-col">
      <p
        className="font-mono text-[10.5px] uppercase tracking-[0.18em]"
        style={{
          color: card.comingSoon
            ? "var(--color-text-on-cream-muted)"
            : "var(--color-accent)",
        }}
      >
        {card.eyebrow}
      </p>
      <h2
        className="mt-3 text-[22px] font-semibold tracking-tight"
        style={{
          color: card.comingSoon
            ? "var(--color-text-on-cream-secondary)"
            : "var(--color-text-on-cream)",
          letterSpacing: "-0.018em",
        }}
      >
        {card.title}
      </h2>
      <p
        className="mt-2 text-[14.5px] leading-[1.55]"
        style={{ color: "var(--color-text-on-cream-secondary)" }}
      >
        {card.desc}
      </p>
      <span
        className="mt-auto pt-6 font-mono text-[12.5px]"
        style={{
          color: card.comingSoon
            ? "var(--color-text-on-cream-muted)"
            : "var(--color-accent)",
        }}
      >
        {card.cta}
      </span>
    </div>
  );

  const sharedStyle: React.CSSProperties = {
    backgroundColor: "rgba(255,255,255,0.45)",
    border: "1px solid var(--color-border-light)",
    borderRadius: "var(--radius-md)",
    padding: "22px",
    minHeight: "180px",
    display: "block",
    height: "100%",
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{
        duration: prefersReduced ? 0 : 0.5,
        delay: prefersReduced ? 0 : index * 0.06,
        ease: EASE,
      }}
      className={wide ? "sm:col-span-2" : ""}
      role="listitem"
    >
      {card.comingSoon ? (
        <div style={sharedStyle} aria-disabled="true">
          {inner}
        </div>
      ) : (
        <Link
          to={card.to}
          style={sharedStyle}
          className="group transition-colors hover:shadow-card-hover"
          onMouseEnter={(e) => {
            e.currentTarget.style.borderColor =
              "var(--color-border-light-strong)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.borderColor = "var(--color-border-light)";
          }}
        >
          {inner}
        </Link>
      )}
    </motion.div>
  );
}
