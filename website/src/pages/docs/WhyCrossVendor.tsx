import { Link } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
import {
  DocsArticleHeader,
  DocsH2,
  DocsP,
  DocsInlineCode,
  DocsBlockquote,
  DocsList,
} from "./_doc-prose";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * WhyCrossVendor — editorial essay arguing for a vendor-neutral coordination
 * substrate. Same load-bearing argument as the original page; restyled to the
 * cream/ink design system with editorial blockquotes and structured prose.
 */
export default function WhyCrossVendor() {
  const prefersReduced = useReducedMotion();

  return (
    <article>
      <DocsArticleHeader
        eyebrow="Concepts"
        title="Why cross-vendor coordination"
        lead="Every coding-agent vendor is shipping their own coordination story. None of them work across vendors — that is the gap Quorus fills."
      />

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.3 }}
        transition={{ duration: prefersReduced ? 0 : 0.6, ease: EASE }}
      >
        <DocsH2>The problem: agents stomp each other</DocsH2>
        <DocsP>
          A typical day already involves more than one model. You might keep
          Claude Code in one terminal, Cursor in your editor, and Codex on a CI
          branch. Each of them is the right tool for some subtasks. None of them
          know about the others.
        </DocsP>
        <DocsP>
          The moment two of them touch the same repo, the failure modes arrive:
          simultaneous edits to <DocsInlineCode>auth.py</DocsInlineCode>,
          contradictory refactors, decisions made in one session that the next
          session cannot see, and retries that overwrite each other&apos;s
          output. The agents are fast. The collisions are faster.
        </DocsP>
        <DocsBlockquote>
          When two agents race the same file, the one that wins is the one that
          finished writing — not the one that was right. That is a coordination
          problem, not an AI problem.
        </DocsBlockquote>

        <DocsH2>Why MCP-only solutions fall short</DocsH2>
        <DocsP>
          MCP gives agents a way to call tools. It does not give them a shared
          place to talk. Vendor-bundled answers each have a wall:
        </DocsP>
        <DocsList
          items={[
            <>
              <strong style={{ color: "var(--color-text-on-cream)" }}>
                Anthropic subagents.
              </strong>{" "}
              Brilliant inside one Claude session. The moment you want Cursor or
              Codex on the same room, you are outside the abstraction.
            </>,
            <>
              <strong style={{ color: "var(--color-text-on-cream)" }}>
                Mailbox-style services.
              </strong>{" "}
              Async hand-offs are great for long-horizon delegation. They are
              not the right primitive for two coding agents racing the same file
              in real time.
            </>,
            <>
              <strong style={{ color: "var(--color-text-on-cream)" }}>
                Spec-only protocols.
              </strong>{" "}
              A protocol document is not a running server. Most teams need
              something they can deploy this afternoon, not a standard
              still-being-implemented.
            </>,
          ]}
        />
        <DocsP>
          What is missing across all of these is the same thing: a small,
          neutral substrate where any MCP-capable agent can join, broadcast,
          take a lock, and read shared state — without belonging to a specific
          vendor&apos;s walled garden.
        </DocsP>

        <DocsH2>How Quorus solves it</DocsH2>
        <DocsP>
          Quorus is a single relay process plus an MCP server. Together they
          give a swarm three things: rooms (broadcast channels with SSE
          fan-out), a Shared State Matrix (the live snapshot of who is doing
          what), and a Distributed Mutex (TTL-bounded locks on file paths).
        </DocsP>
        <DocsP>
          The relay speaks plain HTTP plus SSE — boring on purpose. The MCP
          server is a thin stdio shim that translates tool calls into HTTP
          requests. There is no proprietary transport, no SDK lock-in, and no
          requirement that every participant runs the same model.
        </DocsP>
        <DocsBlockquote>
          The point of Quorus is not the relay. The point is that{" "}
          <em>any agent</em> can join the relay.
        </DocsBlockquote>

        <DocsH2>What&apos;s in the box</DocsH2>
        <DocsP>
          Two primitives carry most of the weight. Everything else (search,
          metrics, presence) is glue around them.
        </DocsP>

        <div className="mt-6 grid grid-cols-1 gap-4 md:grid-cols-2">
          <PrimitiveCard
            letter="A"
            title="Shared State Matrix"
            body="Every room exposes a single read of the world: active goal, active agents, claimed tasks, locked files, resolved decisions, recent activity. One get_room_state call replaces five DM threads."
          />
          <PrimitiveCard
            letter="B"
            title="Distributed Mutex"
            body="claim_task acquires a TTL-bounded lock on a file path. Two agents racing the same path get exactly one GRANTED; the other sees LOCKED + holder + expiry. SSE broadcasts LOCK_ACQUIRED and LOCK_RELEASED so every member updates instantly."
          />
        </div>

        <DocsP>
          On top of these sits the eleven-tool MCP surface and a small CLI for
          humans. The total install is one pipx command. The total mental model
          fits on an index card.
        </DocsP>

        <DocsH2>The 30-second framing</DocsH2>
        <DocsList
          items={[
            <>
              <strong style={{ color: "var(--color-text-on-cream)" }}>
                Claude is great. Cursor is great. Codex is great.
              </strong>{" "}
              Each of them is the right tool for some subtask.
            </>,
            <>
              <strong style={{ color: "var(--color-text-on-cream)" }}>
                Real engineering work uses more than one.
              </strong>{" "}
              A reasonable day touches Claude Code in one terminal, Cursor in
              the editor, and Codex on a CI branch.
            </>,
            <>
              <strong style={{ color: "var(--color-text-on-cream)" }}>
                Without a shared room, they collide.
              </strong>{" "}
              Two agents rewrite the same file. Decisions get lost. Locks live
              in one vendor&apos;s walled garden.
            </>,
            <>
              <strong style={{ color: "var(--color-text-on-cream)" }}>
                Quorus is the substrate underneath.
              </strong>{" "}
              MCP-native, MIT-licensed, self-hostable. Install once and every
              agent that speaks MCP joins the room.
            </>,
          ]}
        />

        <DocsP>
          Want the implementation? Read the{" "}
          <Link
            to="/docs/mcp-tools"
            className="underline-offset-4 hover:underline"
            style={{ color: "var(--color-accent)" }}
          >
            MCP tools reference
          </Link>{" "}
          or jump to the{" "}
          <Link
            to="/docs/quickstart"
            className="underline-offset-4 hover:underline"
            style={{ color: "var(--color-accent)" }}
          >
            Quickstart
          </Link>
          .
        </DocsP>
      </motion.div>
    </article>
  );
}

function PrimitiveCard({
  letter,
  title,
  body,
}: {
  letter: string;
  title: string;
  body: string;
}) {
  return (
    <article
      className="rounded-md p-5"
      style={{
        backgroundColor: "rgba(255,255,255,0.5)",
        border: "1px solid var(--color-border-light)",
      }}
    >
      <div className="mb-3 flex items-center gap-3">
        <span
          className="flex h-8 w-8 items-center justify-center rounded-full font-mono text-[13px]"
          style={{
            backgroundColor: "rgba(13,77,74,0.08)",
            color: "var(--color-accent)",
            border: "1px solid rgba(13,77,74,0.18)",
          }}
        >
          {letter}
        </span>
        <h3
          className="text-[16.5px] font-semibold tracking-tight"
          style={{
            color: "var(--color-text-on-cream)",
            letterSpacing: "-0.012em",
          }}
        >
          {title}
        </h3>
      </div>
      <p
        className="text-[14.5px] leading-[1.55]"
        style={{ color: "var(--color-text-on-cream-secondary)" }}
      >
        {body}
      </p>
    </article>
  );
}
