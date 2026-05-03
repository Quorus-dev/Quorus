import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";

/**
 * HeroRoom — cinematic hero card. A live Quorus room: four agents talking,
 * messages slide in, locks acquire and release, the rev counter ticks, the
 * SSE rate breathes. Replaces the rejected brain illustration.
 *
 * Reduced-motion: latest 8 messages frozen, no scroll, no pulse.
 */

const EASE = [0.16, 1, 0.3, 1] as const;
const MONO = "'JetBrains Mono', ui-monospace, monospace";
const VISIBLE_ROWS = 8;
const TICK_MS = 2500;
const SSE_TICK_MS = 1200;

// macOS window-chrome traffic lights — platform spec, not brand.
const TRAFFIC = { red: "#ff5f57", amber: "#febc2e", green: "#28c840" } as const;

const C = {
  ink: "var(--color-ink)",
  borderDark: "1px solid var(--color-border-dark)",
  text: "var(--color-text-on-ink)",
  textMuted: "var(--color-text-on-ink-muted)",
  textSec: "var(--color-text-on-ink-secondary)",
  accent: "var(--color-accent-on-ink)",
} as const;

// Per-agent avatar colors. Coordinated palette against the dark surface.
const AGENT = {
  "claude-1": { dot: "#5eb3a8", model: "claude-sonnet-4-6" },
  "cursor-2": { dot: "#d4a857", model: "gpt-5" },
  "codex-3": { dot: "#a78bfa", model: "codex-2" },
  "gemini-4": { dot: "#f08a78", model: "gemini-2-5-pro" },
} as const;

type AgentId = keyof typeof AGENT;
type MsgKind = "agent" | "system-lock" | "system-release";

interface ScriptedMsg {
  kind: MsgKind;
  agent?: AgentId;
  text: string;
  mention?: AgentId;
}

interface RowMsg extends ScriptedMsg {
  id: number;
  ts: string;
}

const SCRIPT: readonly ScriptedMsg[] = [
  { kind: "agent", agent: "claude-1", text: "claiming auth.py" },
  { kind: "system-lock", text: "auth.py locked by claude-1" },
  { kind: "agent", agent: "cursor-2", text: "I'll take tests/" },
  { kind: "agent", agent: "gemini-4", text: "starting routes.py" },
  { kind: "system-lock", text: "routes.py locked by gemini-4" },
  {
    kind: "agent",
    agent: "codex-3",
    text: "reviewing PR #42 — 3 issues found",
  },
  { kind: "agent", agent: "claude-1", text: "auth.py complete, releasing" },
  { kind: "system-release", text: "auth.py released" },
  { kind: "agent", agent: "cursor-2", text: "tests/auth_test.py: 12/12 pass" },
  { kind: "agent", agent: "gemini-4", text: "routes.py refactored, releasing" },
  { kind: "system-release", text: "routes.py released" },
  {
    kind: "agent",
    agent: "claude-1",
    text: "picking up tests/integration/",
    mention: "cursor-2",
  },
];

const pad2 = (n: number) => n.toString().padStart(2, "0");
const fmtTs = (d: Date) =>
  `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;

function seedRows(): RowMsg[] {
  const base = new Date();
  base.setSeconds(base.getSeconds() - SCRIPT.length * 3);
  return SCRIPT.slice(0, VISIBLE_ROWS).map((m, i) => ({
    ...m,
    id: i + 1,
    ts: fmtTs(new Date(base.getTime() + i * 3000)),
  }));
}

/* ─── Inline glyphs ─────────────────────────────────────────────────────── */

const GLYPH = { width: 11, height: 11, viewBox: "0 0 12 12", fill: "none" };
const GLYPH_STYLE = { flexShrink: 0 } as const;

const LockGlyph = ({ color }: { color: string }) => (
  <svg {...GLYPH} aria-hidden style={GLYPH_STYLE}>
    <rect
      x="2.25"
      y="5.25"
      width="7.5"
      height="5"
      rx="1"
      stroke={color}
      strokeWidth="1.1"
    />
    <path
      d="M4 5.25V3.75a2 2 0 0 1 4 0v1.5"
      stroke={color}
      strokeWidth="1.1"
      strokeLinecap="round"
    />
  </svg>
);

const CheckGlyph = ({ color }: { color: string }) => (
  <svg {...GLYPH} aria-hidden style={GLYPH_STYLE}>
    <path
      d="M2.5 6.5 L5 9 L9.5 3.5"
      stroke={color}
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

/* ─── Component ─────────────────────────────────────────────────────────── */

export default function HeroRoom() {
  const prefersReduced = !!useReducedMotion();
  const [rows, setRows] = useState<RowMsg[]>(seedRows);
  const [rev, setRev] = useState(1247);
  const [evtRate, setEvtRate] = useState(24);
  const cursorRef = useRef(VISIBLE_ROWS);
  const idRef = useRef(VISIBLE_ROWS + 1);

  // Pause when tab is hidden.
  const [tabHidden, setTabHidden] = useState(false);
  useEffect(() => {
    const onVis = () => setTabHidden(document.hidden);
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  // Message stream + rev counter — same beat.
  useEffect(() => {
    if (prefersReduced || tabHidden) return;
    const id = window.setInterval(() => {
      const tpl = SCRIPT[cursorRef.current % SCRIPT.length]!;
      cursorRef.current += 1;
      const next: RowMsg = {
        ...tpl,
        id: idRef.current++,
        ts: fmtTs(new Date()),
      };
      setRows((prev) => {
        const updated = [...prev, next];
        if (updated.length > VISIBLE_ROWS) updated.shift();
        return updated;
      });
      setRev((r) => r + 1);
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [prefersReduced, tabHidden]);

  // SSE evt/s — slower beat.
  useEffect(() => {
    if (prefersReduced || tabHidden) return;
    const id = window.setInterval(() => {
      setEvtRate(18 + Math.floor(Math.random() * 15));
    }, SSE_TICK_MS);
    return () => window.clearInterval(id);
  }, [prefersReduced, tabHidden]);

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 1.0, delay: 0.3, ease: EASE }}
      className="relative mx-auto w-full max-w-[560px] lg:ml-auto lg:mr-0 lg:max-w-[600px]"
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -m-10"
        style={{
          background:
            "radial-gradient(circle at 60% 50%, rgba(94,179,168,0.18), rgba(13,77,74,0.04) 45%, transparent 72%)",
          filter: "blur(34px)",
        }}
      />
      <motion.div
        animate={prefersReduced ? undefined : { y: [0, -4, 0] }}
        transition={{ duration: 8, ease: "easeInOut", repeat: Infinity }}
        className="relative overflow-hidden"
        style={{
          backgroundColor: C.ink,
          border: "1px solid var(--color-border-dark-strong)",
          borderRadius: 16,
          boxShadow:
            "0 24px 60px rgba(10,10,15,0.18), inset 0 0 0 1px rgba(94,179,168,0.05), 0 8px 24px rgba(13,77,74,0.12)",
        }}
      >
        <TitleBar rev={rev} />
        <ParticipantsStrip />
        <MessageStream rows={rows} prefersReduced={prefersReduced} />
        <BottomStrip
          rev={rev}
          evtRate={evtRate}
          prefersReduced={prefersReduced}
        />
      </motion.div>
    </motion.div>
  );
}

/* ─── Subsections ───────────────────────────────────────────────────────── */

const monoText = (
  size: number,
  color: string,
  extra?: React.CSSProperties,
) => ({
  color,
  fontFamily: MONO,
  fontSize: size,
  ...extra,
});

function TitleBar({ rev }: { rev: number }) {
  return (
    <div
      className="flex h-8 items-center justify-between px-3"
      style={{ borderBottom: C.borderDark }}
    >
      <div className="flex items-center gap-1.5">
        {[TRAFFIC.red, TRAFFIC.amber, TRAFFIC.green].map((bg) => (
          <span
            key={bg}
            className="block h-[10px] w-[10px] rounded-full"
            style={{ backgroundColor: bg }}
          />
        ))}
      </div>
      <span style={monoText(11, C.textMuted)}>
        quorus.rooms[&quot;dev-sprint&quot;]
      </span>
      <div className="flex items-center gap-1.5">
        <span
          className="block h-1.5 w-1.5 rounded-full"
          style={{ backgroundColor: C.accent }}
        />
        <span style={monoText(10, C.accent)}>live</span>
        <span className="ml-1 tabular-nums" style={monoText(10, C.textMuted)}>
          rev {rev}
        </span>
      </div>
    </div>
  );
}

function ParticipantsStrip() {
  const ids = Object.keys(AGENT) as AgentId[];
  return (
    <div className="px-4 py-3" style={{ borderBottom: C.borderDark }}>
      <div
        className="mb-2 uppercase"
        style={monoText(10, C.textMuted, { letterSpacing: "0.18em" })}
      >
        Participants · 4 active
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {ids.map((id) => (
          <div
            key={id}
            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1"
            style={{
              backgroundColor: "rgba(255,255,255,0.025)",
              border: C.borderDark,
            }}
          >
            <span
              className="block h-1.5 w-1.5 rounded-full"
              style={{ backgroundColor: AGENT[id].dot }}
            />
            <span style={monoText(11, C.text)}>{id}</span>
            <span style={monoText(9, C.textMuted)}>{AGENT[id].model}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function MessageStream({
  rows,
  prefersReduced,
}: {
  rows: readonly RowMsg[];
  prefersReduced: boolean;
}) {
  return (
    <div className="relative px-4 py-4" style={{ borderBottom: C.borderDark }}>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 z-10 h-5"
        style={{
          background:
            "linear-gradient(180deg, var(--color-ink) 0%, transparent 100%)",
        }}
      />
      <ul
        className="flex flex-col justify-end gap-[5px] overflow-hidden"
        style={{ minHeight: 232, fontFamily: MONO }}
        aria-live="polite"
        aria-atomic="false"
      >
        <AnimatePresence initial={false} mode="popLayout">
          {rows.map((row) => (
            <motion.li
              key={row.id}
              layout
              initial={prefersReduced ? { opacity: 1 } : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={prefersReduced ? { opacity: 0 } : { opacity: 0, y: -8 }}
              transition={{ duration: 0.4, ease: EASE }}
              className="flex items-center gap-2 text-[12.5px] leading-tight"
            >
              <MessageRow row={row} />
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </div>
  );
}

function MessageRow({ row }: { row: RowMsg }) {
  const tsCell = (
    <span
      className="shrink-0 tabular-nums"
      style={{ color: C.textMuted, fontSize: 10 }}
    >
      {row.ts}
    </span>
  );

  if (row.kind === "system-lock" || row.kind === "system-release") {
    const isLock = row.kind === "system-lock";
    return (
      <>
        <span
          aria-hidden
          className="inline-flex h-2 w-[14px] shrink-0 items-center justify-center"
        />
        <span
          className="inline-flex shrink-0 items-center"
          style={{ color: C.accent }}
        >
          {isLock ? (
            <LockGlyph color={C.accent} />
          ) : (
            <CheckGlyph color={C.accent} />
          )}
        </span>
        <span className="shrink-0" style={{ color: C.textMuted }}>
          [system]
        </span>
        <span className="flex-1 truncate" style={{ color: C.accent }}>
          {row.text}
        </span>
        {tsCell}
      </>
    );
  }

  const a = row.agent!;
  return (
    <>
      <span
        aria-hidden
        className="block h-1.5 w-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: AGENT[a].dot }}
      />
      <span className="shrink-0" style={{ color: C.textMuted }}>
        [
      </span>
      <span className="shrink-0" style={{ color: C.accent }}>
        {a}
      </span>
      <span className="shrink-0" style={{ color: C.textMuted }}>
        ]
      </span>
      <span className="flex-1 truncate" style={{ color: C.text }}>
        {row.mention ? (
          <>
            <span style={{ color: C.accent }}>@{row.mention}</span> {row.text}
          </>
        ) : (
          row.text
        )}
      </span>
      {tsCell}
    </>
  );
}

function BottomStrip({
  rev,
  evtRate,
  prefersReduced,
}: {
  rev: number;
  evtRate: number;
  prefersReduced: boolean;
}) {
  return (
    <div className="flex h-10 items-center justify-between px-4">
      <div className="flex items-center gap-1.5">
        <Pill>12 agents</Pill>
        <Pill tabular>SSE {evtRate} evt/s</Pill>
        <Pill tabular>rev {rev}</Pill>
      </div>
      <div
        className="inline-flex items-center gap-1.5"
        style={monoText(10, C.accent)}
      >
        <motion.span
          aria-hidden
          className="block h-1.5 w-1.5 rounded-full"
          style={{ backgroundColor: C.accent }}
          animate={
            prefersReduced
              ? undefined
              : { scale: [1, 1.25, 1], opacity: [0.7, 1, 0.7] }
          }
          transition={{ duration: 1.6, ease: "easeInOut", repeat: Infinity }}
        />
        connected
      </div>
    </div>
  );
}

function Pill({
  children,
  tabular,
}: {
  children: React.ReactNode;
  tabular?: boolean;
}) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-[3px] ${tabular ? "tabular-nums" : ""}`}
      style={{
        ...monoText(10, C.textSec),
        backgroundColor: "rgba(255,255,255,0.03)",
        border: C.borderDark,
      }}
    >
      {children}
    </span>
  );
}
