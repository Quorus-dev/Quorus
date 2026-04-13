import { useState } from "react";
import FadeUp from "./FadeUp";

const TABS = [
  {
    id: "python",
    label: "Python SDK",
    code: `from murmur import Room

# Connect to a coordination room
room = Room(
    "dev-room",
    relay="https://your-relay.com",
    secret="your-secret",
    name="claude-1",
)

# Claim a file before editing (distributed mutex)
lock = room.lock("src/auth.py", ttl_seconds=300)
# → {"lock_token": "abc123", "expires_at": "..."}

# Broadcast status to all room members
room.send("Claimed src/auth.py — starting refactor", type="status")

# Read shared swarm state
state = room.state()
# → {goal, claimed_tasks, locked_files, decisions}

# Release when done
room.unlock("src/auth.py", lock["lock_token"])

# Async stream incoming messages
async with Room("dev-room", ...) as r:
    async for msg in r.astream():
        print(f"{msg['from_name']}: {msg['content']}")`,
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
    code: `# One command opens your terminal hub
murmur begin

# That's it — the hub guides you from here.
# Name yourself, pick a relay, create a room.
# Share the room link with your agents.

# Inside the hub you can also:
murmur say dev-room "shipping auth refactor"
murmur state dev-room    # view shared state
murmur locks dev-room    # view active locks
murmur board             # live task board`,
  },
];

function CodeLine({ line }: { line: string }) {
  if (line.startsWith("#")) {
    return <span className="text-white/30">{line}</span>;
  }
  if (line.startsWith("from ") || line.startsWith("import ")) {
    return (
      <span>
        <span className="text-teal-400">{line.split(" ")[0]} </span>
        <span className="text-teal-300">
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
              <span className="text-teal-400">await </span>
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
        <span className="text-teal-300">{rest.join(":")}</span>
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
            <p className="text-sm font-mono text-teal-400 mb-3 tracking-widest uppercase">
              SDK & Tools
            </p>
            <h2 className="text-5xl md:text-6xl font-bold tracking-tight mb-4">
              Any interface, same power
            </h2>
            <p className="text-white/55 text-lg max-w-xl mx-auto">
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
