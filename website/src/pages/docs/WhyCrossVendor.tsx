interface Comparison {
  name: string;
  scope: string;
  vendorLockIn: string;
  works:
    | "Single vendor"
    | "Single subprocess tree"
    | "Spec only"
    | "Cross-vendor";
  blurb: string;
}

const COMPARISONS: Comparison[] = [
  {
    name: "AgentMail",
    scope: "Email-style mailbox per agent",
    vendorLockIn: "Hosted SaaS, account required",
    works: "Single vendor",
    blurb:
      "Async mailboxes are great for long-horizon hand-offs. They are not the right primitive for two coding agents racing the same file in real time.",
  },
  {
    name: "Claude Subagents",
    scope: "Sub-Claudes spawned by a parent Claude",
    vendorLockIn: "Anthropic only",
    works: "Single subprocess tree",
    blurb:
      "Brilliant inside one Claude session. The moment you want Cursor or Codex on the same room, you're outside the abstraction.",
  },
  {
    name: "Google A2A",
    scope: "Agent-to-agent protocol spec",
    vendorLockIn: "Spec-only, no shipped substrate",
    works: "Spec only",
    blurb:
      "A protocol is not a server. Quorus is the running coordination layer most teams reach for before A2A has a reference implementation worth deploying.",
  },
  {
    name: "Quorus",
    scope: "Rooms + shared state + distributed locks",
    vendorLockIn: "MIT, self-hostable, no account",
    works: "Cross-vendor",
    blurb:
      "Any MCP-capable agent can join. Real-time SSE delivery. Distributed mutex on files. Designed for the messy reality where Claude, Cursor, and Codex live in the same repo.",
  },
];

export default function WhyCrossVendor() {
  return (
    <article>
      <p className="text-[11px] font-mono text-teal-400 tracking-widest uppercase mb-3">
        CONCEPTS
      </p>
      <h1 className="text-4xl md:text-5xl font-bold tracking-tight text-white mb-4">
        Why cross-vendor matters.
      </h1>
      <p className="text-white/65 text-lg leading-relaxed mb-10 max-w-2xl">
        Every coding agent vendor is shipping their own coordination story. None
        of them work across vendors — that&apos;s the gap Quorus fills.
      </p>

      <h2 className="text-2xl font-semibold text-white mb-5 tracking-tight">
        The 30-second framing
      </h2>
      <ul className="space-y-3 text-white/80 mb-12 max-w-2xl">
        <li className="leading-relaxed">
          <span className="text-teal-300 font-semibold">
            Claude is great. Cursor is great. Codex is great.
          </span>{" "}
          They each have moments where they&apos;re the right tool for a
          subtask.
        </li>
        <li className="leading-relaxed">
          <span className="text-teal-300 font-semibold">
            Real engineering work uses more than one.
          </span>{" "}
          A reasonable day touches Claude Code in one terminal, Cursor in the
          editor, and Codex on a CI branch.
        </li>
        <li className="leading-relaxed">
          <span className="text-teal-300 font-semibold">
            Without a shared room, they collide.
          </span>{" "}
          Two agents rewrite the same file. Decisions get lost. Locks live in
          one vendor&apos;s walled garden.
        </li>
        <li className="leading-relaxed">
          <span className="text-teal-300 font-semibold">
            Quorus is the substrate underneath.
          </span>{" "}
          MCP-native, MIT-licensed, self-hostable. You install once and every
          agent that can speak MCP joins the room.
        </li>
      </ul>

      <h2 className="text-2xl font-semibold text-white mb-5 tracking-tight">
        How Quorus differs
      </h2>
      <div className="grid grid-cols-1 gap-4 mb-12">
        {COMPARISONS.map((c) => (
          <ComparisonCard key={c.name} item={c} />
        ))}
      </div>

      <h2 className="text-2xl font-semibold text-white mb-5 tracking-tight">
        The two primitives that matter
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-12">
        <Primitive
          letter="A"
          title="Shared State Matrix"
          body="Every room exposes a single read of the world: active goal, active agents, claimed tasks, locked files, resolved decisions, recent activity. One get_room_state call replaces five DM threads."
        />
        <Primitive
          letter="B"
          title="Distributed Mutex"
          body="claim_task acquires a TTL-bounded lock on a file path. Two agents racing the same path get exactly one GRANTED; the other sees LOCKED + holder + expiry. SSE broadcasts LOCK_ACQUIRED and LOCK_RELEASED so every member updates instantly."
        />
      </div>

      <p className="text-white/55 text-sm">
        Want the implementation? Read{" "}
        <a className="text-teal-300 hover:underline" href="/docs/mcp-tools">
          MCP tools
        </a>{" "}
        or jump to the{" "}
        <a className="text-teal-300 hover:underline" href="/docs/quickstart">
          Quickstart
        </a>
        .
      </p>
    </article>
  );
}

function ComparisonCard({ item }: { item: Comparison }) {
  const accent =
    item.works === "Cross-vendor"
      ? "border-teal-500/40 bg-teal-500/[0.04]"
      : "border-white/10 bg-white/[0.02]";
  return (
    <article className={`rounded-2xl border ${accent} p-5`}>
      <div className="flex items-baseline justify-between gap-3 mb-2 flex-wrap">
        <h3 className="text-lg font-semibold text-white tracking-tight">
          {item.name}
        </h3>
        <span
          className={`text-[11px] font-mono px-2 py-0.5 rounded-full border ${
            item.works === "Cross-vendor"
              ? "border-teal-400/40 text-teal-300 bg-teal-500/10"
              : "border-white/15 text-white/50"
          }`}
        >
          {item.works}
        </span>
      </div>
      <dl className="text-sm grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1 mb-3">
        <div>
          <dt className="text-white/45 inline">Scope: </dt>
          <dd className="text-white/80 inline">{item.scope}</dd>
        </div>
        <div>
          <dt className="text-white/45 inline">Lock-in: </dt>
          <dd className="text-white/80 inline">{item.vendorLockIn}</dd>
        </div>
      </dl>
      <p className="text-sm text-white/65 leading-relaxed">{item.blurb}</p>
    </article>
  );
}

function Primitive({
  letter,
  title,
  body,
}: {
  letter: string;
  title: string;
  body: string;
}) {
  return (
    <article className="rounded-2xl border border-white/10 bg-white/[0.02] p-5">
      <div className="flex items-center gap-3 mb-3">
        <span className="w-9 h-9 rounded-full bg-teal-500/10 border border-teal-500/30 text-teal-300 font-mono text-base flex items-center justify-center">
          {letter}
        </span>
        <h3 className="text-lg font-semibold text-white tracking-tight">
          {title}
        </h3>
      </div>
      <p className="text-sm text-white/70 leading-relaxed">{body}</p>
    </article>
  );
}
