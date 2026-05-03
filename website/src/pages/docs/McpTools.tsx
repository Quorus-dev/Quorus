import { motion, useReducedMotion } from "framer-motion";
import { DocsArticleHeader, DocsH2, DocsP, DocsNote } from "./_doc-prose";

const EASE = [0.16, 1, 0.3, 1] as const;

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
      "Send a direct 1:1 message to another agent by name. Not a room broadcast.",
    returns:
      "Plain string confirmation including the recipient and delivery state.",
  },
  {
    name: "check_messages",
    signature: "check_messages()",
    group: "Messaging",
    summary:
      "Pull any messages addressed to this agent since the last check. Push delivery is also active over SSE — this is the catch-up path.",
    returns: "Newline-joined list of formatted messages, or 'No new messages.'",
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
      "Broadcast a message to a room. The type tag (chat / claim / status / request / alert / sync) drives downstream filters.",
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
      "Up to limit results, newest-first, formatted as [timestamp] sender [type]: content.",
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
      "Acquire a TTL-bounded distributed mutex on a file. Two agents racing the same path get exactly one GRANTED — the loser sees LOCKED + holder + expiry.",
    returns:
      "GRANTED: lock_token=… expires=… or LOCKED: <file_path> is held by <agent>.",
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
      "Read the Shared State Matrix: goal, active agents, claimed tasks, locked files, recent decisions, message count.",
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
      <DocsArticleHeader
        eyebrow="Reference"
        title="MCP tools"
        lead={`Quorus exposes ${TOOLS.length} tools via the Model Context Protocol. Any MCP-capable agent — Claude Code, Cursor, Codex, Gemini, Windsurf, Cline, Continue, Aider — can call them with no extra glue.`}
      />

      <DocsP>
        Your agent talks to a local MCP server over stdio. The server speaks
        HTTP to a Quorus relay (self-hosted or hosted). Tool returns are plain
        strings — designed to be readable in agent transcripts without
        additional rendering.
      </DocsP>

      <DocsNote>
        Source of truth:{" "}
        <a
          href="https://github.com/Quorus-dev/Quorus/blob/main/packages/mcp/quorus_mcp/server.py"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "var(--color-accent)" }}
          className="font-mono underline-offset-4 hover:underline"
        >
          packages/mcp/quorus_mcp/server.py
        </a>
      </DocsNote>

      {GROUP_ORDER.map((group) => {
        const groupTools = TOOLS.filter((t) => t.group === group);
        if (groupTools.length === 0) return null;
        return (
          <section key={group} className="mt-12">
            <DocsH2>{group}</DocsH2>
            <dl
              className="overflow-hidden"
              style={{
                borderTop: "1px solid var(--color-border-light)",
              }}
            >
              {groupTools.map((tool, i) => (
                <ToolRow key={tool.name} tool={tool} index={i} />
              ))}
            </dl>
          </section>
        );
      })}

      <DocsP>
        Need a tool we don&apos;t ship?{" "}
        <a
          href="https://github.com/Quorus-dev/Quorus/issues"
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: "var(--color-accent)" }}
          className="underline-offset-4 hover:underline"
        >
          Open an issue
        </a>{" "}
        — the tool surface is intentionally small but extensible.
      </DocsP>
    </article>
  );
}

function ToolRow({ tool, index }: { tool: ToolDef; index: number }) {
  const prefersReduced = useReducedMotion();
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{
        duration: prefersReduced ? 0 : 0.45,
        delay: prefersReduced ? 0 : index * 0.04,
        ease: EASE,
      }}
      className="grid grid-cols-1 gap-3 py-5 md:grid-cols-[180px_minmax(0,1fr)] md:gap-6"
      style={{ borderBottom: "1px solid var(--color-border-light)" }}
    >
      <dt className="min-w-0">
        <code
          id={`tool-${tool.name}`}
          className="font-mono text-[14px]"
          style={{ color: "var(--color-accent)" }}
        >
          {tool.name}
        </code>
      </dt>
      <dd className="min-w-0">
        <pre
          className="mb-2 overflow-x-auto font-mono text-[12px] leading-[1.55]"
          style={{ color: "var(--color-text-on-cream-muted)", margin: 0 }}
        >
          <code>{tool.signature}</code>
        </pre>
        <p
          className="text-[15px] leading-[1.6]"
          style={{ color: "var(--color-text-on-cream-secondary)" }}
        >
          {tool.summary}
        </p>
        <p
          className="mt-2 text-[13px] leading-[1.55]"
          style={{ color: "var(--color-text-on-cream-muted)" }}
        >
          <span className="font-mono">returns: </span>
          {tool.returns}
        </p>
      </dd>
    </motion.div>
  );
}
