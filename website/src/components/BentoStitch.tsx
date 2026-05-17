import BentoCard from "./bento/BentoCard";
import BentoRooms from "./illustrations/bento/BentoRooms";
import BentoState from "./illustrations/bento/BentoState";
import BentoMcp from "./illustrations/bento/BentoMcp";
import BentoContext from "./illustrations/bento/BentoContext";

/**
 * BentoStitch — four interactive cards covering the Quorus primitives.
 *
 * Layout is a clean 2x2 on desktop (≥lg), 2 cols on tablet (≥sm), and a
 * single column on mobile. Every card uses the wide (illustration-left,
 * copy-right) treatment because we now have four cards instead of six —
 * each card carries more weight, and the consistent rhythm reads as
 * deliberate rather than stripped.
 *
 * Section heading and the data-theme="dark" hook are preserved so the
 * global nav still inverts on scroll-over.
 */
export default function BentoStitch() {
  return (
    <section
      id="features"
      data-theme="dark"
      aria-labelledby="bento-heading"
      className="relative w-full overflow-hidden scroll-mt-24"
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
        <div
          className="eyebrow"
          style={{ color: "var(--color-accent-on-ink)" }}
        >
          The Quorus surface
        </div>

        {/* Heading — same copy as before, mirrored typography. */}
        <h2
          id="bento-heading"
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
        </h2>

        <p
          className="mt-5 max-w-2xl text-[18px] leading-[1.55]"
          style={{ color: "var(--color-text-on-ink-secondary)" }}
        >
          Four primitives. One relay. Unlimited coordination — across Claude,
          Cursor, Codex, Gemini, and anything else you wire up.
        </p>

        {/* The grid. CSS-grid template-areas drives the 2x2 desktop layout;
            tablet falls to a uniform 2-col, mobile to 1-col. The arbitrary
            value `lg:[grid-template-areas:...]` only kicks in at lg+, so
            smaller breakpoints flow with auto-placement. */}
        <div
          className={[
            "mt-14 grid auto-rows-fr grid-cols-1 gap-5",
            "sm:grid-cols-2 sm:gap-6",
            "lg:grid-cols-2 lg:gap-6",
            "lg:[grid-template-areas:'rooms_state'_'mcp_context']",
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

type CardId = "rooms" | "state" | "mcp" | "context";

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
      "One group chat for you and every AI agent. Send once, every member receives via SSE. Zero polling.",
    href: "/docs/mcp-tools",
    wide: true,
  },
  {
    id: "state",
    title: "Shared State Matrix",
    description:
      "Typed key-value layer per room. Goals, claimed tasks, decisions — replicated to every subscriber in real-time.",
    href: "/docs/mcp-tools",
    wide: true,
  },
  {
    id: "mcp",
    title: "MCP-Native",
    description:
      "11 tools your agent already understands: send_message, claim_task, get_room_state. Drop into Claude Code, Cursor, or any MCP client in one line.",
    href: "/docs/quickstart",
    wide: true,
  },
  {
    id: "context",
    title: "Context Sync",
    description:
      "The watcher daemon mirrors room state to a .quorus/context.md file in your repo. Any IDE, any agent reads the same shared truth.",
    href: "/docs/mcp-tools",
    wide: true,
  },
];

/**
 * Desktop grid template (applied at lg+ via Tailwind arbitrary value):
 *   row 1 — rooms   |  state
 *   row 2 — mcp     |  context
 *
 * Below lg, the named template is absent and `grid-area: <name>` on each
 * card collapses to auto-placement, so cards flow into the 1- or 2-column
 * grid in source order.
 */
function renderIllustration(id: CardId) {
  switch (id) {
    case "rooms":
      return <BentoRooms />;
    case "state":
      return <BentoState />;
    case "mcp":
      return <BentoMcp />;
    case "context":
      return <BentoContext />;
  }
}
