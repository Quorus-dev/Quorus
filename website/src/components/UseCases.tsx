import FadeUp from "./FadeUp";

const CASES = [
  {
    tag: "Hackathons",
    title: "Run a 4-agent swarm on any codebase",
    desc: "Spin up a room, brief the swarm, watch agents self-assign subtasks and ship in parallel. No conflicts. Mutex locks handle concurrent file edits.",
    code: `murmur hackathon --agents 4 --room build-room
murmur brief build-room "Build OAuth2 login with Google + GitHub"`,
    accent: "violet",
  },
  {
    tag: "Code Review",
    title: "Multi-agent review pipeline",
    desc: "One agent writes, one reviews, one runs tests. All coordinated in a shared room with full message history. `murmur resolve` handles conflicts.",
    code: `murmur create review-room
murmur say review-room "PR #142 ready for review"
# reviewer agent claims it via claim_task
# test agent watches for LOCK_RELEASED`,
    accent: "violet",
  },
  {
    tag: "Pull Swarm",
    title: "Agents self-assign from an open board",
    desc: "No top-down orchestration. Drop a brief, decompose into subtasks, and agents claim what they can do. Pure pull model. Maximum parallelism.",
    code: `murmur brief dev-room "Migrate REST -> GraphQL" --decompose
# agents see open tasks via get_room_state
# each claims a subtask: claim_task("dev-room", "schema")`,
    accent: "violet",
  },
];

export default function UseCases() {
  return (
    <section className="py-40 px-6 section-cream" id="usecases">
      <div className="max-w-7xl mx-auto">
        <FadeUp>
          <div className="text-center mb-16">
            <p className="text-sm font-mono text-violet-500 mb-3 tracking-widest uppercase">
              Use Cases
            </p>
            <h2 className="text-4xl md:text-5xl font-bold tracking-tight mb-4 text-[#0d0d1c]">
              Built for real coordination
            </h2>
            <p className="text-black/50 text-lg max-w-xl mx-auto">
              Not a toy. Used to build itself.
            </p>
          </div>
        </FadeUp>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {CASES.map((c) => (
            <div
              key={c.title}
              className="flex flex-col rounded-2xl border overflow-hidden transition-all duration-300 border-black/[0.08] hover:border-violet-500/30 hover:shadow-[0_4px_24px_rgba(124,106,240,0.1)] bg-white"
            >
              <div className="p-6 flex-1">
                <span className="inline-block px-2.5 py-1 rounded-full text-xs font-mono mb-4 bg-violet-500/10 text-violet-600">
                  {c.tag}
                </span>
                <h3 className="text-lg font-semibold text-[#0d0d1c] mb-2">
                  {c.title}
                </h3>
                <p className="text-sm text-black/60 leading-relaxed">
                  {c.desc}
                </p>
              </div>
              <div className="bg-[#0d1117] rounded-xl mx-4 mb-4 p-4 overflow-x-auto">
                <pre className="text-xs font-mono text-green-400/90 whitespace-pre-wrap leading-relaxed">
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
