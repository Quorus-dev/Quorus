import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";

/**
 * HeroRoom — cinematic hero card. A live Quorus room: four agents talking,
 * task claims fire, context.md sync events broadcast, the rev counter ticks,
 * the SSE rate breathes. Replaces the rejected brain illustration.
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
  "claude-1": { dot: "#5eb3a8", model: "claude-sonnet-4.5" },
  "cursor-2": { dot: "#d4a857", model: "gpt-4o" },
  "codex-3": { dot: "#a78bfa", model: "codex-2" },
  "gemini-4": { dot: "#f08a78", model: "gemini-2-5-pro" },
} as const;

type AgentId = keyof typeof AGENT;
type MsgKind = "agent" | "system";

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
  {
    kind: "agent",
    agent: "claude-1",
    text: "claim_task: refactor auth module",
  },
  { kind: "system", text: "context.md synced · rev 1248" },
  { kind: "agent", agent: "cursor-2", text: "starting on test coverage" },
  { kind: "agent", agent: "gemini-4", text: "search_room: rate limit" },
  { kind: "system", text: "4 matches across 3 files" },
  {
    kind: "agent",
    agent: "codex-3",
    text: "reviewing PR #42 — 3 issues found",
  },
  {
    kind: "agent",
    agent: "claude-1",
    text: "auth refactor done · summary posted",
  },
  { kind: "system", text: "rev 1249 · 24 evt/s" },
  { kind: "agent", agent: "cursor-2", text: "tests/auth_test.py: 12/12 pass" },
  { kind: "agent", agent: "gemini-4", text: "posting room summary" },
  { kind: "system", text: "room metrics: 240 msg/min" },
  {
    kind: "agent",
    agent: "claude-1",
    text: "@cursor-2 pair on integration edge cases?",
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
    <div className="relative mx-auto w-full max-w-[560px] lg:ml-auto lg:mr-0 lg:max-w-[600px]">
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
        <BorderBeam prefersReduced={prefersReduced} />
        <TitleBar rev={rev} />
        <ParticipantsStrip />
        <MessageStream rows={rows} prefersReduced={prefersReduced} />
        <BottomStrip
          rev={rev}
          evtRate={evtRate}
          prefersReduced={prefersReduced}
        />
      </motion.div>
    </div>
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

  if (row.kind === "system") {
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
          <CheckGlyph color={C.accent} />
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

/* ─── Border Beam ───────────────────────────────────────────────────────── */
/**
 * Magic-UI inspired border beam. A short glowing teal segment travels around
 * the rounded card perimeter on a continuous loop. Sits behind content but
 * above background. Subtle by design — low alpha, hairline stroke.
 *
 * Implementation: rounded `<rect>` with `pathLength=100` so the dash math is
 * resolution-independent (no need to measure the actual perimeter, which
 * varies with card width). A short visible dash (BEAM=14% of perimeter) sits
 * inside a long gap (the rest), and animating `strokeDashoffset` from 0 → 100
 * slides the lit segment around the perimeter once per loop. The stroke also
 * uses a soft drop-shadow filter for the comet glow.
 *
 * Reduced-motion: renders a static accent stroke (no traveling segment).
 */
function BorderBeam({ prefersReduced }: { prefersReduced: boolean }) {
  // Half the stroke inset so the stroke aligns flush with the card's r=16.
  const STROKE = 1.5;
  const INSET = STROKE / 2;
  const BEAM = 14; // % of perimeter that is visibly lit
  const GAP = 100 - BEAM;

  return (
    <svg
      aria-hidden
      className="pointer-events-none absolute inset-0 z-0 h-full w-full overflow-visible"
      preserveAspectRatio="none"
      style={{ borderRadius: 16 }}
    >
      {/* Static base stroke — barely visible accent ring. Always renders so
       * the card edge has a faint teal warmth even when motion is off. */}
      <rect
        x={INSET}
        y={INSET}
        width={`calc(100% - ${STROKE}px)`}
        height={`calc(100% - ${STROKE}px)`}
        rx={16 - INSET}
        ry={16 - INSET}
        fill="none"
        stroke={C.accent}
        strokeOpacity={0.06}
        strokeWidth={STROKE}
      />
      {/* Traveling comet segment. Skipped under reduced motion. */}
      {!prefersReduced && (
        <rect
          x={INSET}
          y={INSET}
          width={`calc(100% - ${STROKE}px)`}
          height={`calc(100% - ${STROKE}px)`}
          rx={16 - INSET}
          ry={16 - INSET}
          fill="none"
          stroke={C.accent}
          strokeOpacity={0.85}
          strokeWidth={STROKE}
          strokeLinecap="round"
          pathLength={100}
          strokeDasharray={`${BEAM} ${GAP}`}
          style={{
            // Soft glow without an SVG <filter> — cheaper, no flicker.
            filter: "drop-shadow(0 0 4px rgba(94,179,168,0.55))",
          }}
        >
          <animate
            attributeName="stroke-dashoffset"
            from="0"
            to="-100"
            dur="6s"
            repeatCount="indefinite"
          />
        </rect>
      )}
    </svg>
  );
}
