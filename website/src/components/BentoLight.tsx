import { motion, useReducedMotion } from "framer-motion";
import { RoomsIllustration } from "./illustrations/Rooms";
import { LocksIllustration } from "./illustrations/Locks";
import { StateMatrixIllustration } from "./illustrations/StateMatrix";
import { McpIllustration } from "./illustrations/Mcp";
import { CodeSyncIllustration } from "./illustrations/CodeSync";
import { SseIllustration } from "./illustrations/Sse";

const EASE = [0.16, 1, 0.3, 1] as const;

type Card = {
  id: string;
  title: string;
  desc: string;
  illustration: React.ComponentType;
  // CSS grid placement at the >=lg breakpoint. Mobile and md fall back to
  // single/two-col stacks via the wrapper class names below.
  area: string;
};

const CARDS: Card[] = [
  {
    id: "rooms",
    title: "Rooms & Fan-out",
    desc: "Coordinated channels for any number of agents — join, broadcast, leave. SSE delivers updates without polling.",
    illustration: RoomsIllustration,
    area: "rooms",
  },
  {
    id: "locks",
    title: "Distributed Locks",
    desc: "Two agents can't claim the same file. Locks are atomic, scoped, and auto-released on disconnect.",
    illustration: LocksIllustration,
    area: "locks",
  },
  {
    id: "state",
    title: "Shared State Matrix",
    desc: "A typed key-value layer per room. Optimistic writes, last-writer-wins by revision, replicated to every subscriber.",
    illustration: StateMatrixIllustration,
    area: "state",
  },
  {
    id: "mcp",
    title: "MCP Native",
    desc: "Twelve coordination tools surfaced as a Model Context Protocol server. Drop into Claude Code, Cursor, Codex.",
    illustration: McpIllustration,
    area: "mcp",
  },
  {
    id: "code",
    title: "Code-aware Sync",
    desc: "Diff-aware messages that survive context windows. Agents share intent and patches, not just text.",
    illustration: CodeSyncIllustration,
    area: "code",
  },
  {
    id: "sse",
    title: "Real-time SSE",
    desc: "One persistent connection per agent. Sub-100ms event delivery on commodity hardware. No polling, ever.",
    illustration: SseIllustration,
    area: "sse",
  },
];

export default function BentoLight() {
  return (
    <section
      id="features"
      aria-labelledby="bento-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: "var(--color-cream)" }}
    >
      <div className="mx-auto max-w-7xl px-6 py-24 lg:py-32">
        {/* Section header */}
        <div className="mb-14 grid grid-cols-1 items-end gap-6 lg:mb-20 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <p
              className="eyebrow mb-4"
              style={{ color: "var(--color-accent)" }}
            >
              SIX PRIMITIVES · ONE RELAY
            </p>
            <h2
              id="bento-heading"
              style={{
                color: "var(--color-text-on-cream)",
                fontWeight: 600,
                letterSpacing: "-0.022em",
                lineHeight: 1.05,
                fontSize: "clamp(36px, 4.5vw, 56px)",
              }}
            >
              Everything your swarm needs.
            </h2>
          </div>
          <p
            className="max-w-md text-[16px] leading-[1.6] lg:col-span-5 lg:justify-self-end lg:text-right"
            style={{ color: "var(--color-text-on-cream-secondary)" }}
          >
            Quorus ships the small set of primitives every multi-agent system
            re-invents. Use them à la carte — or all at once.
          </p>
        </div>

        {/* Asymmetric bento grid.
            Mobile: 1 col stacked.
            md (640+): 2 col with select tall cells.
            lg (1024+): 6-col grid with explicit areas so cards span unevenly.
        */}
        <div
          className={[
            "grid gap-4",
            "grid-cols-1",
            "md:grid-cols-2",
            "lg:grid-cols-6 lg:grid-rows-[260px_260px]",
          ].join(" ")}
          style={{
            gridTemplateAreas: `
              "rooms rooms rooms locks locks  state"
              "mcp   code  code  code  sse    sse"
            `,
          }}
        >
          {CARDS.map((card, i) => (
            <motion.article
              key={card.id}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.3 }}
              transition={{
                duration: 0.6,
                delay: i * 0.05,
                ease: EASE,
              }}
              className={[
                "group relative flex flex-col overflow-hidden rounded-xl border bg-white/45 p-5 transition-colors lg:p-6",
                // mobile + md heights so cards have visual weight
                "min-h-[280px]",
                // hover: subtle border darken + tiny lift via box-shadow
                "hover:border-[color:rgba(13,77,74,0.25)] hover:shadow-card-hover",
              ].join(" ")}
              style={{
                gridArea: card.area,
                borderColor: "var(--color-border-light)",
              }}
            >
              {/* Illustration on top */}
              <div className="relative mb-5 flex-1 overflow-hidden rounded-md">
                <BentoIllustration card={card} />
              </div>

              {/* Copy at bottom */}
              <div>
                <h3
                  className="mb-1.5 text-[16px] font-semibold tracking-tight"
                  style={{ color: "var(--color-text-on-cream)" }}
                >
                  {card.title}
                </h3>
                <p
                  className="text-[13.5px] leading-[1.55]"
                  style={{ color: "var(--color-text-on-cream-secondary)" }}
                >
                  {card.desc}
                </p>
              </div>
            </motion.article>
          ))}
        </div>
      </div>
    </section>
  );
}

function BentoIllustration({ card }: { card: Card }) {
  const prefersReduced = useReducedMotion();
  const Comp = card.illustration;

  return (
    <motion.div
      whileHover={prefersReduced ? undefined : { y: -2 }}
      transition={{ duration: 0.3, ease: EASE }}
      className="absolute inset-0 flex items-center justify-center"
      style={{
        // Tint the inset slightly so each illustration sits on its own surface
        backgroundColor: "rgba(10,10,15,0.018)",
        borderRadius: 8,
      }}
    >
      <Comp />
    </motion.div>
  );
}
