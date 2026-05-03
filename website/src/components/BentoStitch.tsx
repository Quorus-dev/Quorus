import { motion } from "framer-motion";
import BentoCard from "./bento/BentoCard";
import BentoRooms from "./illustrations/bento/BentoRooms";
import BentoLocks from "./illustrations/bento/BentoLocks";
import BentoState from "./illustrations/bento/BentoState";
import BentoMcp from "./illustrations/bento/BentoMcp";
import BentoCodeSync from "./illustrations/bento/BentoCodeSync";
import BentoSse from "./illustrations/bento/BentoSse";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * BentoStitch — six interactive cards covering the Quorus primitives.
 *
 * Layout is asymmetric on desktop (≥1024px), 2 cols on tablet, 1 col on
 * mobile. Two cards (`rooms`, `state`) get a wider footprint and the
 * left-illustration treatment. The remaining four are uniform stacked cards.
 *
 * Replaces the previous flat Stitch image. Section heading and the
 * data-theme="dark" hook are preserved so the global nav still inverts on
 * scroll-over.
 */
export default function BentoStitch() {
  return (
    <section
      data-theme="dark"
      aria-labelledby="bento-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: "var(--color-ink)" }}
    >
      {/* Atmosphere — same family as ControlCenterDark / CTADark.
          Two off-axis radials and a low-opacity grain. Restrained. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 60% 40% at 18% 12%, rgba(94,179,168,0.09), transparent 70%), radial-gradient(ellipse 70% 50% at 82% 92%, rgba(94,179,168,0.06), transparent 70%)",
        }}
      />

      <div className="relative mx-auto max-w-7xl px-6 py-24 lg:py-32">
        {/* Eyebrow */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.5, ease: EASE }}
          className="eyebrow"
          style={{ color: "var(--color-accent-on-ink)" }}
        >
          The Quorus surface
        </motion.div>

        {/* Heading — same copy as before, mirrored typography. */}
        <motion.h2
          id="bento-heading"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.65, delay: 0.05, ease: EASE }}
          className="mt-3 max-w-3xl"
          style={{
            color: "var(--color-text-on-ink)",
            fontWeight: 600,
            letterSpacing: "-0.02em",
            lineHeight: 1.02,
            fontSize: "clamp(36px, 4.6vw, 60px)",
          }}
        >
          Everything your swarm needs.
        </motion.h2>

        <motion.p
          initial={{ opacity: 0, y: 14 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.6, delay: 0.12, ease: EASE }}
          className="mt-5 max-w-2xl text-[18px] leading-[1.55]"
          style={{ color: "var(--color-text-on-ink-secondary)" }}
        >
          Six primitives. One relay. Unlimited coordination — across Claude,
          Cursor, Codex, Gemini, and anything else you wire up.
        </motion.p>

        {/* The grid. CSS-grid template-areas drives the asymmetric desktop
            layout; tablet falls to a uniform 2-col, mobile to 1-col. The
            arbitrary-value `lg:[grid-template-areas:...]` only kicks in at
            lg+, so smaller breakpoints flow with auto-placement. */}
        <div
          className={[
            "mt-14 grid auto-rows-fr grid-cols-1 gap-5",
            "sm:grid-cols-2 sm:gap-6",
            "lg:grid-cols-6 lg:gap-6",
            "lg:[grid-template-areas:'rooms_rooms_locks_state_state_state'_'mcp_mcp_codesync_codesync_sse_sse']",
          ].join(" ")}
        >
          {CARDS.map((card, i) => (
            <BentoCard
              key={card.id}
              id={card.id}
              title={card.title}
              description={card.description}
              href={card.href}
              illustration={renderIllustration(card.id)}
              wide={card.wide}
              area={card.id}
              index={i}
            />
          ))}
        </div>
      </div>
    </section>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Card data + helpers
// ────────────────────────────────────────────────────────────────────────────

type CardId = "rooms" | "locks" | "state" | "mcp" | "codesync" | "sse";

type Card = {
  id: CardId;
  title: string;
  description: string;
  href: string;
  /** Wider card with side-by-side illustration on desktop. */
  wide?: boolean;
};

const CARDS: ReadonlyArray<Card> = [
  {
    id: "rooms",
    title: "Rooms & Fan-out",
    description:
      "Coordinated channels for any number of agents. Join, broadcast, leave — SSE delivers updates without polling.",
    href: "/docs/mcp-tools",
    wide: true,
  },
  {
    id: "locks",
    title: "Context Sync",
    description:
      "Quorus mirrors room state to a .quorus/context.md file in your repo. Any IDE, any agent reads the same shared context.",
    href: "/docs/mcp-tools",
  },
  {
    id: "state",
    title: "Shared State Matrix",
    description:
      "A typed key-value layer per room. Optimistic writes, last-writer-wins by revision, replicated to every subscriber.",
    href: "/docs/mcp-tools",
    wide: true,
  },
  {
    id: "mcp",
    title: "MCP Native",
    description:
      "Ships as a Model Context Protocol server. Drop into Claude Code, Cursor, or any MCP client in one line.",
    href: "/docs/quickstart",
  },
  {
    id: "codesync",
    title: "Code-aware Sync",
    description:
      "Diff-aware messaging across agents. Share file context and review notes without copy-pasting.",
    href: "/docs/mcp-tools",
  },
  {
    id: "sse",
    title: "Real-time SSE",
    description:
      "Server-Sent Events stream room state at <50ms latency. No WebSocket complexity.",
    href: "/docs/why-cross-vendor",
  },
];

/**
 * Desktop grid template (applied at lg+ via Tailwind arbitrary value):
 *   row 1 — rooms (2 cols)  locks (1 col)  state (3 cols)
 *   row 2 — mcp   (2 cols)  codesync (2 cols)  sse (2 cols)
 *
 * Below lg, the named template is absent and `grid-area: <name>` on each
 * card collapses to auto-placement, so cards flow into the 1- or 2-column
 * grid in source order.
 */
function renderIllustration(id: CardId) {
  switch (id) {
    case "rooms":
      return <BentoRooms />;
    case "locks":
      return <BentoLocks />;
    case "state":
      return <BentoState />;
    case "mcp":
      return <BentoMcp />;
    case "codesync":
      return <BentoCodeSync />;
    case "sse":
      return <BentoSse />;
  }
}
