
import FadeUp from "./FadeUp";

const CASES = [
  {
    tag: "Hackathons",
    title: "Run a 4-agent swarm on any codebase",
    desc: "Spin up a room, brief the swarm, watch agents self-assign subtasks and ship in parallel. No conflicts — mutex locks handle concurrent file edits.",
    code: `murmur hackathon --agents 4 --room build-room
murmur brief build-room "Build OAuth2 login with Google + GitHub"`,
    accent: "violet",
  },
  {
    tag: "Code Review",
    title: "Multi-agent review pipeline",
    desc: "One agent writes, one reviews, one runs tests — all coordinated in a shared room with full message history. `murmur resolve` handles conflicts.",
    code: `murmur create review-room
murmur say review-room "PR #142 ready for review"
# reviewer agent claims it via claim_task
# test agent watches for LOCK_RELEASED`,
    accent: "violet",
  },
  {
    tag: "Pull Swarm",
    title: "Agents self-assign from an open board",
    desc: "No top-down orchestration. Drop a brief, decompose into subtasks, and agents claim what they can do. Pure pull model — maximum parallelism.",
    code: `murmur brief dev-room "Migrate REST → GraphQL" --decompose
# agents see open tasks via get_room_state
# each claims a subtask: claim_task("dev-room", "schema")`,
    accent: "violet",
  },
];

export default function UseCases() {
  return (
    <section className="py-32 px-6" id="usecases">
      <div className="max-w-7xl mx-auto">
        <FadeUp>
          <div className="text-center mb-16">
            <p className="text-sm font-mono text-amber-400 mb-3 tracking-widest uppercase">
              Use Cases
            </p>
            <h2 className="text-4xl md:text-5xl font-bold tracking-tight mb-4">
              Built for real coordination
            </h2>
            <p className="text-white/40 text-lg max-w-xl mx-auto">
              Not a toy. Used to build itself.
            </p>
          </div>
        </FadeUp>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {CASES.map((c) => (
            <div
              key={c.title}
              className={`flex flex-col rounded-2xl border overflow-hidden transition-all duration-300 ${
                c.accent === "violet"
                  ? "border-amber-500/20 hover:border-amber-500/40 hover:shadow-[0_0_40px_rgba(217,119,6,0.08)]"
                  : "border-amber-500/20 hover:border-amber-500/40 hover:shadow-[0_0_40px_rgba(217,119,6,0.08)]"
              } bg-white/[0.02]`}
            >
              <div className="p-6 flex-1">
                <span
                  className={`inline-block px-2.5 py-1 rounded-full text-xs font-mono mb-4 ${
                    c.accent === "violet"
                      ? "bg-amber-500/15 text-amber-300"
                      : "bg-amber-500/15 text-amber-300"
                  }`}
                >
                  {c.tag}
                </span>
                <h3 className="text-lg font-semibold text-white mb-2">
                  {c.title}
                </h3>
                <p className="text-sm text-white/40 leading-relaxed">
                  {c.desc}
                </p>
              </div>
              <div className="code-block mx-4 mb-4 p-4 rounded-xl">
                <pre className="text-xs font-mono text-green-400/80 overflow-x-auto whitespace-pre-wrap leading-relaxed">
                  {c.code}
                </pre>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
