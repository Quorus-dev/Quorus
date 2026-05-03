/**
 * Internal panels for ControlCenterDark — extracted to keep the section file
 * under the 500-line budget. Not exported from the public component barrel.
 */
import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";

const COLORS = {
  ink2: "#14141c",
  borderDark: "rgba(255,255,255,0.08)",
  textPrimary: "#f5f1ea",
  textSecondary: "#a8a8b0",
  textMuted: "#6a6a72",
  accentOnInk: "#5eb3a8",
  accentOnInkDim: "rgba(94,179,168,0.55)",
} as const;

const EASE = [0.16, 1, 0.3, 1] as const;
const MONO = "'JetBrains Mono', ui-monospace, monospace";
const SANS = "'Plus Jakarta Sans', system-ui, sans-serif";

// ---------- Shared: BreathingDot ----------

export function BreathingDot({ color }: { color: string }) {
  const prefersReduced = useReducedMotion();
  return (
    <motion.span
      aria-hidden
      className="inline-block h-1.5 w-1.5 rounded-full"
      style={{ backgroundColor: color }}
      animate={prefersReduced ? undefined : { opacity: [0.45, 1, 0.45] }}
      transition={{ duration: 2.4, ease: "easeInOut", repeat: Infinity }}
    />
  );
}

// ---------- Stream column ----------

const STREAM_TEMPLATES = [
  "room.{room} · agent.{agent} joined",
  "room.{room} · lock.acquire {file}",
  "room.{room} · agent.{agent} → say {snippet}",
  "room.{room} · lock.release {file}",
  "room.{room} · task.claim #{n}",
  "room.{room} · sse.fanout {count} subscribers",
  "room.{room} · agent.{agent} heartbeat",
  "room.{room} · task.complete #{n}",
  "room.{room} · room.state.snapshot",
] as const;

const ROOMS = ["dev-sprint", "demo-fri", "may4-bench"] as const;
const AGENTS = ["claude-4-7", "codex-1", "cursor-2", "gpt-5"] as const;
const FILES = [
  "relay.py",
  "rooms/state.py",
  "mcp/server.py",
  "tests/test_relay.py",
] as const;
const SNIPPETS = [
  '"on it, picking up #42"',
  '"plan posted, starting now"',
  '"shipped a674f76"',
  '"ack for prod migration?"',
] as const;

type StreamRow = { id: number; ts: string; text: string };

function pad2(n: number) {
  return n.toString().padStart(2, "0");
}

function fmtTs(d: Date) {
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

function pick<T>(arr: readonly T[], i: number): T {
  return arr[i % arr.length];
}

let _streamId = 0;
function nextStreamRow(seed: number, when: Date): StreamRow {
  const tpl = pick(STREAM_TEMPLATES, seed);
  const text = tpl
    .replace("{room}", pick(ROOMS, seed >> 1))
    .replace("{agent}", pick(AGENTS, seed >> 2))
    .replace("{file}", pick(FILES, seed >> 3))
    .replace("{snippet}", pick(SNIPPETS, seed >> 4))
    .replace("{n}", String(40 + (seed % 12)))
    .replace("{count}", String(3 + (seed % 5)));
  return { id: ++_streamId, ts: fmtTs(when), text };
}

export function StreamColumn({ paused }: { paused: boolean }) {
  const prefersReduced = useReducedMotion();
  const [rows, setRows] = useState<StreamRow[]>(() => {
    const now = Date.now();
    const seeded: StreamRow[] = [];
    for (let i = 11; i >= 0; i--) {
      seeded.push(nextStreamRow(i * 7 + 3, new Date(now - i * 1500)));
    }
    return seeded;
  });
  const seedRef = useRef(100);

  useEffect(() => {
    if (paused || prefersReduced) return;
    const id = window.setInterval(() => {
      setRows((prev) => {
        const next = nextStreamRow(seedRef.current++, new Date());
        const updated = [...prev, next];
        if (updated.length > 14) updated.shift();
        return updated;
      });
    }, 1500);
    return () => window.clearInterval(id);
  }, [paused, prefersReduced]);

  return (
    <div className="flex h-full flex-col">
      <div className="mb-3 flex items-center justify-between">
        <span
          className="text-[10px] uppercase"
          style={{
            color: COLORS.textMuted,
            fontFamily: MONO,
            letterSpacing: "0.18em",
          }}
        >
          SSE stream
        </span>
        <span
          className="inline-flex items-center gap-1.5 text-[10px]"
          style={{ color: COLORS.accentOnInkDim, fontFamily: MONO }}
        >
          <BreathingDot color={COLORS.accentOnInk} />
          live
        </span>
      </div>

      <div
        className="relative flex-1 overflow-hidden rounded-md"
        style={{
          backgroundColor: "rgba(0,0,0,0.25)",
          border: `1px solid ${COLORS.borderDark}`,
        }}
      >
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 z-10 h-6"
          style={{
            background: `linear-gradient(180deg, ${COLORS.ink2} 0%, transparent 100%)`,
          }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-6"
          style={{
            background: `linear-gradient(0deg, ${COLORS.ink2} 0%, transparent 100%)`,
          }}
        />
        <ul
          className="flex h-full flex-col justify-end gap-[3px] overflow-hidden p-3"
          style={{ fontFamily: MONO }}
          aria-live="polite"
          aria-atomic="false"
        >
          {rows.map((row, i) => {
            const opacity = Math.max(0.25, (i + 1) / rows.length);
            return (
              <motion.li
                key={row.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity, y: 0 }}
                transition={{ duration: 0.4, ease: EASE }}
                className="flex items-baseline gap-2 text-[11px] leading-tight"
                style={{ color: COLORS.textSecondary }}
              >
                <span style={{ color: COLORS.textMuted }}>[{row.ts}]</span>
                <span className="truncate">{row.text}</span>
              </motion.li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

// ---------- Agent grid ----------

type Agent = {
  id: string;
  name: string;
  model: string;
  status: "active" | "idle" | "thinking";
};

const AGENTS_DATA: Agent[] = [
  { id: "a1", name: "frontend-eng", model: "claude-4-7", status: "active" },
  { id: "a2", name: "backend-eng", model: "claude-4-7", status: "thinking" },
  { id: "a3", name: "code-review", model: "codex-1", status: "active" },
  { id: "a4", name: "security", model: "claude-4-7", status: "idle" },
  { id: "a5", name: "devops", model: "gpt-5", status: "active" },
  { id: "a6", name: "product", model: "claude-4-7", status: "thinking" },
];

function statusColor(status: Agent["status"]) {
  switch (status) {
    case "active":
      return COLORS.accentOnInk;
    case "thinking":
      return "#d4a857";
    case "idle":
    default:
      return COLORS.textMuted;
  }
}

export function AgentGrid() {
  return (
    <div className="flex h-full flex-col">
      <div className="mb-3 flex items-center justify-between">
        <span
          className="text-[10px] uppercase"
          style={{
            color: COLORS.textMuted,
            fontFamily: MONO,
            letterSpacing: "0.18em",
          }}
        >
          Agents in room
        </span>
        <span
          className="text-[10px]"
          style={{ color: COLORS.textMuted, fontFamily: MONO }}
        >
          {AGENTS_DATA.length} online
        </span>
      </div>
      <div className="grid flex-1 grid-cols-2 gap-2">
        {AGENTS_DATA.map((agent) => (
          <div
            key={agent.id}
            className="flex flex-col justify-between rounded-md p-3"
            style={{
              backgroundColor: "rgba(255,255,255,0.02)",
              border: `1px solid ${COLORS.borderDark}`,
            }}
          >
            <div className="flex items-center justify-between">
              <span
                className="truncate text-[12px]"
                style={{
                  color: COLORS.textPrimary,
                  fontFamily: SANS,
                  fontWeight: 500,
                }}
              >
                {agent.name}
              </span>
              <BreathingDot color={statusColor(agent.status)} />
            </div>
            <div className="mt-2 flex items-center justify-between">
              <span
                className="truncate text-[10px]"
                style={{ color: COLORS.textMuted, fontFamily: MONO }}
              >
                {agent.model}
              </span>
              <span
                className="text-[9px] uppercase"
                style={{
                  color: statusColor(agent.status),
                  fontFamily: MONO,
                  letterSpacing: "0.12em",
                }}
              >
                {agent.status}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- Lock state column ----------

type LockRow = { id: string; file: string; owner: string; heldFor: number };

const INITIAL_LOCKS: LockRow[] = [
  { id: "l1", file: "relay.py", owner: "backend-eng", heldFor: 18 },
  { id: "l2", file: "rooms/state.py", owner: "frontend-eng", heldFor: 4 },
  { id: "l3", file: "mcp/server.py", owner: "code-review", heldFor: 47 },
  { id: "l4", file: "tests/test_relay.py", owner: "security", heldFor: 9 },
];

function fmtHeld(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${pad2(s)}s`;
}

export function LockState({ paused }: { paused: boolean }) {
  const prefersReduced = useReducedMotion();
  const [locks, setLocks] = useState<LockRow[]>(INITIAL_LOCKS);

  useEffect(() => {
    if (paused || prefersReduced) return;
    const id = window.setInterval(() => {
      setLocks((prev) => prev.map((l) => ({ ...l, heldFor: l.heldFor + 1 })));
    }, 1000);
    return () => window.clearInterval(id);
  }, [paused, prefersReduced]);

  return (
    <div className="flex h-full flex-col">
      <div className="mb-3 flex items-center justify-between">
        <span
          className="text-[10px] uppercase"
          style={{
            color: COLORS.textMuted,
            fontFamily: MONO,
            letterSpacing: "0.18em",
          }}
        >
          Distributed locks
        </span>
        <span
          className="text-[10px]"
          style={{ color: COLORS.textMuted, fontFamily: MONO }}
        >
          {locks.length} held
        </span>
      </div>
      <ul className="flex flex-1 flex-col gap-1.5" style={{ fontFamily: MONO }}>
        {locks.map((lock) => (
          <li
            key={lock.id}
            className="rounded-md px-3 py-2.5"
            style={{
              backgroundColor: "rgba(255,255,255,0.02)",
              border: `1px solid ${COLORS.borderDark}`,
            }}
          >
            <div className="flex items-center justify-between gap-2">
              <span
                className="truncate text-[11px]"
                style={{ color: COLORS.textPrimary }}
              >
                {lock.file}
              </span>
              <span
                className="shrink-0 text-[10px] tabular-nums"
                style={{ color: COLORS.accentOnInk }}
              >
                {fmtHeld(lock.heldFor)}
              </span>
            </div>
            <div
              className="mt-1 truncate text-[10px]"
              style={{ color: COLORS.textMuted }}
            >
              held by {lock.owner}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
