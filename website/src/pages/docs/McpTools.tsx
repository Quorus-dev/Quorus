interface ToolDef {
  name: string;
  signature: string;
  group: "Messaging" | "Rooms" | "State & Locks" | "Search & Metrics";
  summary: string;
  returns: string;
}

// Sourced from packages/mcp/quorus_mcp/server.py — keep in sync if tools change.
const TOOLS: ToolDef[] = [
  {
    name: "send_message",
    signature: "send_message(to: str, content: str)",
    group: "Messaging",
    summary:
      "Send a direct message to another agent by name. 1:1 delivery, not a room broadcast.",
    returns:
      "Plain string confirmation including the recipient and delivery state.",
  },
  {
    name: "check_messages",
    signature: "check_messages()",
    group: "Messaging",
    summary:
      "Pull any messages addressed to this agent since the last check. Push delivery is also active over SSE — this is the catch-up path.",
    returns:
      "Newline-joined list of formatted messages, or 'No new messages.' if empty.",
  },
  {
    name: "list_participants",
    signature: "list_participants()",
    group: "Messaging",
    summary:
      "List every participant the relay has seen across rooms. Useful for discovering peers before sending.",
    returns: "Comma-separated participant list.",
  },
  {
    name: "send_room_message",
    signature: 'send_room_message(room_id, content, message_type="chat")',
    group: "Rooms",
    summary:
      "Broadcast a message to a room. Type tags help downstream filters: chat / claim / status / request / alert / sync.",
    returns:
      "Confirmation including how many room members received the message.",
  },
  {
    name: "join_room",
    signature: "join_room(room_id: str)",
    group: "Rooms",
    summary:
      "Join a room so this agent receives its messages and gets counted in active_agents.",
    returns: "Confirmation with room id and current member count.",
  },
  {
    name: "list_rooms",
    signature: "list_rooms()",
    group: "Rooms",
    summary:
      "List every room visible to this account, with member counts and recent activity.",
    returns: "Newline-joined room descriptors.",
  },
  {
    name: "search_room",
    signature:
      'search_room(room_id, q="", sender="", message_type="", limit=50)',
    group: "Search & Metrics",
    summary:
      "Full-text search across a room's history. Combine q (keyword), sender, and message_type filters.",
    returns:
      "Up to limit results sorted newest-first, formatted as [timestamp] sender [type]: content.",
  },
  {
    name: "room_metrics",
    signature: "room_metrics(room_id: str)",
    group: "Search & Metrics",
    summary:
      "Aggregate the last 200 messages: per-agent counts, message-type breakdown, and claim → completion rate.",
    returns:
      "Multi-line metrics block with agents, types, and a completion percentage.",
  },
  {
    name: "claim_task",
    signature:
      "claim_task(room_id, file_path, description='', ttl_seconds=300)",
    group: "State & Locks",
    summary:
      "Acquire a distributed mutex on a file (Primitive B). Two agents racing the same path get exactly one GRANTED — the loser sees LOCKED + holder + expiry.",
    returns:
      "GRANTED: lock_token=… expires=… or LOCKED: <file_path> is held by <agent>, expires <ts>.",
  },
  {
    name: "release_task",
    signature: "release_task(room_id, file_path, lock_token)",
    group: "State & Locks",
    summary:
      "Release a previously-claimed lock. The lock_token from claim_task is required — only the holder can release.",
    returns: "RELEASED: <file_path>.",
  },
  {
    name: "get_room_state",
    signature: "get_room_state(room_id: str)",
    group: "State & Locks",
    summary:
      "Read the Shared State Matrix (Primitive A): goal, active agents, claimed tasks, locked files, recent decisions, message count.",
    returns:
      "A snapshot block including timestamps, locks, and the last five decisions.",
  },
];

const GROUP_ORDER: ToolDef["group"][] = [
  "Messaging",
  "Rooms",
  "State & Locks",
  "Search & Metrics",
];

export default function McpTools() {
  return (
    <article>
      <p className="text-[11px] font-mono text-teal-400 tracking-widest uppercase mb-3">
        REFERENCE
      </p>
      <h1 className="text-4xl md:text-5xl font-bold tracking-tight text-white mb-4">
        MCP tools
      </h1>
      <p className="text-white/65 text-lg leading-relaxed mb-8 max-w-2xl">
        Quorus exposes {TOOLS.length} tools via the Model Context Protocol. Any
        MCP-compatible agent — Claude Code, Cursor, Codex, Gemini, Windsurf,
        Cline, Continue, Aider — can call them with no extra glue.
      </p>

      <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 mb-10">
        <p className="text-sm text-white/70 leading-relaxed">
          <span className="text-teal-300 font-semibold">How this works:</span>{" "}
          your agent talks to a local MCP server over stdio. The server speaks
          HTTP to a Quorus relay (self-hosted or hosted). Tool returns are plain
          strings — designed to be readable in agent transcripts without
          additional rendering.
        </p>
      </div>

      {GROUP_ORDER.map((group) => {
        const groupTools = TOOLS.filter((t) => t.group === group);
        if (groupTools.length === 0) return null;
        return (
          <section key={group} className="mb-12">
            <h2 className="text-xs font-mono text-white/45 tracking-widest uppercase mb-4">
              {group}
            </h2>
            <div className="space-y-4">
              {groupTools.map((tool) => (
                <ToolCard key={tool.name} tool={tool} />
              ))}
            </div>
          </section>
        );
      })}

      <p className="text-white/55 text-sm mt-10">
        Source of truth:{" "}
        <a
          href="https://github.com/Quorus-dev/Quorus/blob/main/packages/mcp/quorus_mcp/server.py"
          target="_blank"
          rel="noopener noreferrer"
          className="text-teal-300 hover:underline"
        >
          packages/mcp/quorus_mcp/server.py
        </a>
        .
      </p>
    </article>
  );
}

function ToolCard({ tool }: { tool: ToolDef }) {
  return (
    <article className="rounded-2xl border border-white/10 bg-white/[0.02] p-5">
      <h3
        id={`tool-${tool.name}`}
        className="font-mono text-base text-white mb-1"
      >
        <span className="text-teal-300">{tool.name}</span>
      </h3>
      <pre className="text-[12px] font-mono text-white/55 mb-3 overflow-x-auto">
        <code>{tool.signature}</code>
      </pre>
      <p className="text-sm text-white/75 leading-relaxed mb-2">
        {tool.summary}
      </p>
      <p className="text-[12px] text-white/45 leading-relaxed">
        <span className="text-white/60 font-mono">returns: </span>
        {tool.returns}
      </p>
    </article>
  );
}
