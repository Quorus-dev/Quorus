import { useEffect, useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;
const INSTALL_CMD =
  'pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"';

/**
 * HeroLight — cream split hero. Left: badge + headline + subhead + waitlist
 * + install command. Right: a debugger-style "watch panel" that morphs JSON
 * room state every couple of seconds. NOT a chat log.
 */
export default function HeroLight() {
  return (
    <section
      aria-labelledby="hero-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: "var(--color-cream)" }}
    >
      {/* Single subtle radial — accent tint at the lower-left, not the
          AI-template centered halo. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-32 -left-32 h-[480px] w-[480px] rounded-full"
        style={{
          background:
            "radial-gradient(circle at 30% 70%, rgba(13,77,74,0.05), transparent 60%)",
        }}
      />

      {/* Faint vertical column rule — editorial accent */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-y-0 right-[8%] hidden w-px lg:block"
        style={{ backgroundColor: "var(--color-border-light)" }}
      />

      <div className="relative mx-auto grid max-w-7xl grid-cols-1 items-center gap-12 px-6 pb-24 pt-32 lg:grid-cols-12 lg:gap-10 lg:pt-40">
        {/* Left column — copy + CTA */}
        <div className="lg:col-span-7">
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: EASE }}
            className="inline-flex items-center gap-2 rounded-full border px-3 py-1"
            style={{ borderColor: "var(--color-border-light-strong)" }}
          >
            <span
              className="block h-1.5 w-1.5 rounded-full"
              style={{ backgroundColor: "var(--color-accent)" }}
            />
            <span
              className="font-mono text-[11px] tracking-wider"
              style={{ color: "var(--color-text-on-cream-secondary)" }}
            >
              OPEN BETA · v0.4 · MIT
            </span>
          </motion.div>

          <motion.h1
            id="hero-heading"
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.05, ease: EASE }}
            className="mt-7"
            style={{
              color: "var(--color-text-on-cream)",
              fontWeight: 600,
              letterSpacing: "-0.022em",
              lineHeight: 0.98,
              fontSize: "clamp(44px, 6vw, 76px)",
            }}
          >
            Coordination Layer
            <br />
            for Agent Teams
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.15, ease: EASE }}
            className="mt-6 max-w-xl text-[18px] leading-[1.55]"
            style={{ color: "var(--color-text-on-cream-secondary)" }}
          >
            Quorus gives your AI swarms rooms, shared state, and real-time
            coordination.{" "}
            <span style={{ color: "var(--color-text-on-cream)" }}>
              Any model. Any machine.
            </span>
          </motion.p>

          {/* Waitlist row — inline email + CTA, style only */}
          <motion.form
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.25, ease: EASE }}
            onSubmit={(e) => e.preventDefault()}
            className="mt-9 flex w-full max-w-md flex-col gap-2 sm:flex-row"
            aria-label="Join the waitlist"
          >
            <input
              type="email"
              placeholder="you@company.com"
              className="h-11 flex-1 rounded-md border bg-white/60 px-4 text-[14px] outline-none transition-colors placeholder:text-slate-400/80 focus:border-[var(--color-accent)] focus:bg-white"
              style={{
                borderColor: "var(--color-border-light-strong)",
                color: "var(--color-text-on-cream)",
              }}
            />
            <button
              type="submit"
              className="h-11 rounded-md px-5 text-[13px] font-medium tracking-tight transition-transform duration-200 hover:-translate-y-px"
              style={{
                backgroundColor: "var(--color-ink)",
                color: "var(--color-cream)",
              }}
            >
              Join waitlist
            </button>
          </motion.form>

          {/* Install command — copyable */}
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.35, ease: EASE }}
            className="mt-5 max-w-md"
          >
            <InstallCommand />
            <p
              className="mt-2 font-mono text-[11px]"
              style={{ color: "var(--color-text-on-cream-muted)" }}
            >
              Or `quorus init` after install. Python 3.10+. MIT.
            </p>
          </motion.div>
        </div>

        {/* Right column — terminal/state panel */}
        <div className="lg:col-span-5">
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.4, ease: EASE }}
            className="mx-auto max-w-[520px] lg:ml-auto lg:mr-0"
          >
            <RoomStatePanel />
          </motion.div>
        </div>
      </div>
    </section>
  );
}

/* ── Install command ─────────────────────────────────────────────────────── */

function InstallCommand() {
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_CMD);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard may be blocked in some contexts — silently no-op.
    }
  };

  return (
    <div
      className="group flex items-center gap-3 rounded-md border px-4 py-3 transition-colors"
      style={{
        backgroundColor: "var(--color-ink-2)",
        borderColor: "var(--color-border-dark)",
      }}
    >
      <span
        className="font-mono text-[12px]"
        style={{ color: "var(--color-accent-on-ink)" }}
      >
        $
      </span>
      <code
        className="flex-1 overflow-x-auto whitespace-nowrap font-mono text-[12px]"
        style={{ color: "var(--color-text-on-ink)" }}
      >
        {INSTALL_CMD}
      </code>
      <button
        type="button"
        onClick={onCopy}
        aria-label={copied ? "Copied" : "Copy install command"}
        className="rounded px-2 py-1 font-mono text-[11px] transition-colors"
        style={{
          color: copied
            ? "var(--color-accent-on-ink)"
            : "var(--color-text-on-ink-secondary)",
        }}
      >
        {copied ? "copied" : "copy"}
      </button>
    </div>
  );
}

/* ── Room state panel — debugger watch ───────────────────────────────────── */

type RoomState = {
  room: string;
  participants: Array<{ name: string; model: string }>;
  locks: Record<string, string>;
  last_message: { from: string; preview: string };
  rev: number;
};

const STATES: RoomState[] = [
  {
    room: "dev-sprint",
    participants: [
      { name: "claude-1", model: "claude-sonnet-4-6" },
      { name: "cursor-2", model: "gpt-5" },
      { name: "codex-3", model: "claude-haiku-4-5" },
    ],
    locks: { "auth.py": "claude-1", "tests/": "cursor-2" },
    last_message: { from: "claude-1", preview: "claiming auth.py" },
    rev: 41,
  },
  {
    room: "dev-sprint",
    participants: [
      { name: "claude-1", model: "claude-sonnet-4-6" },
      { name: "cursor-2", model: "gpt-5" },
      { name: "codex-3", model: "claude-haiku-4-5" },
      { name: "gemini-4", model: "gemini-3-pro" },
    ],
    locks: {
      "auth.py": "claude-1",
      "tests/": "cursor-2",
      "routes.py": "gemini-4",
    },
    last_message: { from: "gemini-4", preview: "joined room" },
    rev: 42,
  },
  {
    room: "dev-sprint",
    participants: [
      { name: "claude-1", model: "claude-sonnet-4-6" },
      { name: "cursor-2", model: "gpt-5" },
      { name: "codex-3", model: "claude-haiku-4-5" },
      { name: "gemini-4", model: "gemini-3-pro" },
    ],
    locks: { "tests/": "cursor-2", "routes.py": "gemini-4" },
    last_message: { from: "claude-1", preview: "released auth.py" },
    rev: 43,
  },
];

function RoomStatePanel() {
  const prefersReduced = useReducedMotion();
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    if (prefersReduced) return;
    const t = setInterval(() => setIdx((i) => (i + 1) % STATES.length), 2800);
    return () => clearInterval(t);
  }, [prefersReduced]);

  const state = STATES[idx];

  // Format like a debugger watch panel — JSON-shaped, expandable feel.
  const json = useMemo(() => formatState(state), [state]);

  return (
    <div
      className="overflow-hidden rounded-xl border shadow-lg"
      style={{
        backgroundColor: "var(--color-ink)",
        borderColor: "var(--color-border-dark-strong)",
      }}
    >
      {/* Title bar */}
      <div
        className="flex items-center gap-3 border-b px-4 py-2.5"
        style={{ borderColor: "var(--color-border-dark)" }}
      >
        <div className="flex gap-1.5">
          <span className="block h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
          <span className="block h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
          <span className="block h-2.5 w-2.5 rounded-full bg-[#28c840]" />
        </div>
        <div
          className="ml-2 flex-1 font-mono text-[11px]"
          style={{ color: "var(--color-text-on-ink-muted)" }}
        >
          watch · quorus.rooms[&quot;{state.room}&quot;]
        </div>
        <div
          className="font-mono text-[10px]"
          style={{ color: "var(--color-accent-on-ink)" }}
        >
          rev {state.rev}
        </div>
      </div>

      {/* Watch body */}
      <div
        key={state.rev}
        className="px-4 py-4 font-mono text-[12px] leading-[1.55]"
        style={{ color: "var(--color-text-on-ink)", minHeight: 290 }}
      >
        {json.map((line, i) => (
          <motion.div
            key={`${state.rev}-${i}`}
            initial={{ opacity: 0, x: -4 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{
              duration: 0.4,
              delay: prefersReduced ? 0 : i * 0.025,
              ease: EASE,
            }}
            style={{ paddingLeft: line.indent * 14 }}
            className="whitespace-pre"
          >
            <KeyValueLine line={line} />
          </motion.div>
        ))}
      </div>

      {/* Bottom strip */}
      <div
        className="flex items-center justify-between border-t px-4 py-2 font-mono text-[10px]"
        style={{
          borderColor: "var(--color-border-dark)",
          color: "var(--color-text-on-ink-muted)",
        }}
      >
        <span>
          <span style={{ color: "var(--color-accent-on-ink)" }}>●</span> relay ·
          connected
        </span>
        <span>{state.participants.length} agents · SSE 12 evt/s</span>
      </div>
    </div>
  );
}

type Line =
  | { kind: "open"; indent: number; key?: string; bracket: "{" | "[" }
  | { kind: "close"; indent: number; bracket: "}" | "]"; comma?: boolean }
  | {
      kind: "field";
      indent: number;
      key: string;
      value: string;
      comma?: boolean;
    }
  | { kind: "raw"; indent: number; text: string };

function formatState(s: RoomState): Line[] {
  const lines: Line[] = [];
  lines.push({ kind: "open", indent: 0, bracket: "{" });
  lines.push({
    kind: "field",
    indent: 1,
    key: "room",
    value: `"${s.room}"`,
    comma: true,
  });
  lines.push({ kind: "open", indent: 1, key: "participants", bracket: "[" });
  s.participants.forEach((p, i) => {
    lines.push({
      kind: "raw",
      indent: 2,
      text: `{ name: "${p.name}", model: "${p.model}" }${i < s.participants.length - 1 ? "," : ""}`,
    });
  });
  lines.push({ kind: "close", indent: 1, bracket: "]", comma: true });
  lines.push({ kind: "open", indent: 1, key: "locks", bracket: "{" });
  const lockEntries = Object.entries(s.locks);
  lockEntries.forEach(([k, v], i) => {
    lines.push({
      kind: "raw",
      indent: 2,
      text: `"${k}": "${v}"${i < lockEntries.length - 1 ? "," : ""}`,
    });
  });
  lines.push({ kind: "close", indent: 1, bracket: "}", comma: true });
  lines.push({ kind: "open", indent: 1, key: "last_message", bracket: "{" });
  lines.push({
    kind: "field",
    indent: 2,
    key: "from",
    value: `"${s.last_message.from}"`,
    comma: true,
  });
  lines.push({
    kind: "field",
    indent: 2,
    key: "preview",
    value: `"${s.last_message.preview}"`,
  });
  lines.push({ kind: "close", indent: 1, bracket: "}" });
  lines.push({ kind: "close", indent: 0, bracket: "}" });
  return lines;
}

function KeyValueLine({ line }: { line: Line }) {
  const KEY_COLOR = "var(--color-accent-on-ink)";
  const PUNCT = "var(--color-text-on-ink-muted)";
  const STRING = "#e8c598"; // warm sand — the only place we use this color
  const NUMBER = "#a8a8b0";

  if (line.kind === "open") {
    return (
      <>
        {line.key && (
          <>
            <span style={{ color: KEY_COLOR }}>{line.key}</span>
            <span style={{ color: PUNCT }}>: </span>
          </>
        )}
        <span style={{ color: PUNCT }}>{line.bracket}</span>
      </>
    );
  }
  if (line.kind === "close") {
    return (
      <span style={{ color: PUNCT }}>
        {line.bracket}
        {line.comma ? "," : ""}
      </span>
    );
  }
  if (line.kind === "field") {
    const isString = line.value.startsWith('"');
    return (
      <>
        <span style={{ color: KEY_COLOR }}>{line.key}</span>
        <span style={{ color: PUNCT }}>: </span>
        <span style={{ color: isString ? STRING : NUMBER }}>{line.value}</span>
        {line.comma && <span style={{ color: PUNCT }}>,</span>}
      </>
    );
  }
  // raw — color-aware: split on quoted segments
  return <RawLine text={line.text} />;
}

function RawLine({ text }: { text: string }) {
  const PUNCT = "var(--color-text-on-ink-muted)";
  const KEY_COLOR = "var(--color-accent-on-ink)";
  const STRING = "#e8c598";

  // Tokenize: keys (identifier:), strings ("..."), and punctuation
  const parts: Array<{ t: string; kind: "string" | "key" | "punct" }> = [];
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if (c === '"') {
      const end = text.indexOf('"', i + 1);
      if (end === -1) {
        parts.push({ t: text.slice(i), kind: "string" });
        break;
      }
      parts.push({ t: text.slice(i, end + 1), kind: "string" });
      i = end + 1;
      continue;
    }
    // identifier followed by ":" → key
    const idMatch = text.slice(i).match(/^([a-zA-Z_][a-zA-Z0-9_]*):\s*/);
    if (idMatch) {
      parts.push({ t: idMatch[1], kind: "key" });
      parts.push({ t: ": ", kind: "punct" });
      i += idMatch[0].length;
      continue;
    }
    parts.push({ t: c, kind: "punct" });
    i += 1;
  }
  return (
    <>
      {parts.map((p, idx) => (
        <span
          key={idx}
          style={{
            color:
              p.kind === "string"
                ? STRING
                : p.kind === "key"
                  ? KEY_COLOR
                  : PUNCT,
          }}
        >
          {p.t}
        </span>
      ))}
    </>
  );
}
