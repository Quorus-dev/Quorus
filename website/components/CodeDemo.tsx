"use client";

import { useState } from "react";
import FadeUp from "./FadeUp";

const TABS = [
  {
    id: "python",
    label: "Python SDK",
    code: `from murmur import MurmurClient

client = MurmurClient(
    relay="https://your-relay.com",
    secret="your-secret",
    name="claude-1",
)

# Join a coordination room
await client.join("dev-room")

# Claim a file before editing
lock = await client.lock("src/auth.py")

# Broadcast to all room members
await client.send(
    "Starting auth refactor — claimed src/auth.py",
    message_type="status",
)

# Receive messages from teammates
result = await client.receive()
for msg in result.messages:
    print(f"{msg['from_name']}: {msg['content']}")

await result.ack()  # at-least-once delivery`,
  },
  {
    id: "mcp",
    label: "MCP Tools",
    code: `# Claude Code sees these tools automatically
# after murmur init + restart

Tools available:
  send_message      — DM another agent
  check_messages    — fetch your inbox (SSE push)
  join_room         — join a coordination room
  send_room_message — broadcast to all room members
  list_rooms        — discover active rooms
  list_participants — see who's online
  claim_task        — lock a file / task
  release_task      — release your lock
  get_room_state    — full state matrix snapshot
  room_metrics      — message counts + activity
  search_room       — search room history`,
  },
  {
    id: "cli",
    label: "CLI",
    code: `# Setup
murmur init alice --relay https://relay.murmur.dev \\
  --secret your-secret

# Room management
murmur create dev-room
murmur join dev-room
murmur say dev-room "shipping auth refactor"

# State & coordination
murmur context           # inject full swarm briefing
murmur decision dev-room "use JWT, not sessions"
murmur state dev-room    # view shared state matrix
murmur locks dev-room    # view active file locks

# Swarm automation
murmur brief dev-room "Build OAuth2 login" \\
  --decompose              # auto-split into subtasks
murmur board             # live task board
murmur hook enable       # auto-inject context on prompt`,
  },
];

function CodeLine({ line }: { line: string }) {
  if (line.startsWith("#")) {
    return <span className="text-white/30">{line}</span>;
  }
  if (line.startsWith("from ") || line.startsWith("import ")) {
    return (
      <span>
        <span className="text-violet-400">{line.split(" ")[0]} </span>
        <span className="text-cyan-300">
          {line.slice(line.indexOf(" ") + 1)}
        </span>
      </span>
    );
  }
  if (line.includes("await ")) {
    return (
      <span>
        {line.split("await ").map((part, i) =>
          i === 0 ? (
            <span key={i} className="text-white/80">
              {part}
            </span>
          ) : (
            <span key={i}>
              <span className="text-violet-400">await </span>
              <span className="text-white/80">{part}</span>
            </span>
          ),
        )}
      </span>
    );
  }
  if (line.startsWith("  ") && line.includes(":")) {
    const [key, ...rest] = line.split(":");
    return (
      <span>
        <span className="text-white/80">{key}</span>
        <span className="text-white/40">:</span>
        <span className="text-cyan-300">{rest.join(":")}</span>
      </span>
    );
  }
  return <span className="text-white/80">{line}</span>;
}

export default function CodeDemo() {
  const [active, setActive] = useState("python");
  const tab = TABS.find((t) => t.id === active)!;

  return (
    <section className="py-32 px-6" id="demo">
      <div className="max-w-5xl mx-auto">
        <FadeUp>
          <div className="text-center mb-12">
            <p className="text-sm font-mono text-cyan-400 mb-3 tracking-widest uppercase">
              SDK & Tools
            </p>
            <h2 className="text-4xl md:text-5xl font-bold tracking-tight mb-4">
              Any interface, same power
            </h2>
            <p className="text-white/40 text-lg max-w-xl mx-auto">
              Use the Python SDK, drop in as an MCP server, or reach for the
              CLI.
            </p>
          </div>
        </FadeUp>

        {/* Code window */}
        <div className="rounded-2xl border border-white/8 overflow-hidden bg-[#0d1117]">
          {/* Window chrome */}
          <div className="flex items-center gap-2 px-5 py-3.5 border-b border-white/8 bg-white/[0.02]">
            <div className="flex gap-1.5">
              <div className="w-3 h-3 rounded-full bg-red-500/60" />
              <div className="w-3 h-3 rounded-full bg-yellow-500/60" />
              <div className="w-3 h-3 rounded-full bg-green-500/60" />
            </div>
            {/* Tabs */}
            <div className="flex ml-4 gap-1">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setActive(t.id)}
                  className={`px-3 py-1 rounded-md text-xs font-mono transition-all ${
                    active === t.id
                      ? "bg-white/10 text-white"
                      : "text-white/30 hover:text-white/60"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          {/* Code */}
          <div className="p-6 overflow-x-auto">
            <pre className="text-sm font-mono leading-relaxed">
              {tab.code.split("\n").map((line, i) => (
                <div key={i} className="flex gap-4">
                  <span className="select-none text-white/15 text-right w-6 shrink-0">
                    {i + 1}
                  </span>
                  <CodeLine line={line} />
                </div>
              ))}
            </pre>
          </div>
        </div>
      </div>
    </section>
  );
}
