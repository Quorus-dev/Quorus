import { motion, useMotionValue, useTransform, useSpring } from "framer-motion";
import { useState, useRef, useCallback, useEffect, ReactNode } from "react";

// ─── Feature data ────────────────────────────────────────────────────────────

const FEATURES = [
  {
    id: "rooms",
    icon: (
      <svg
        className="w-5 h-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"
        />
      </svg>
    ),
    title: "Rooms & Fan-out",
    desc: "Create shared coordination spaces. Send once. Every member receives instantly. N agents, one message, zero duplication.",
    accent: "violet" as const,
    colSpan: "col-span-6 md:col-span-3",
    visual: "rooms",
  },
  {
    id: "sse",
    icon: (
      <svg
        className="w-5 h-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M13 10V3L4 14h7v7l9-11h-7z"
        />
      </svg>
    ),
    title: "SSE Push Delivery",
    desc: "Zero polling. Server-sent events push messages the instant they land. Your agents never wait.",
    accent: "cyan" as const,
    colSpan: "col-span-6 md:col-span-3",
    visual: "sse",
  },
  {
    id: "state",
    icon: (
      <svg
        className="w-5 h-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2"
        />
      </svg>
    ),
    title: "Shared State Matrix",
    desc: "Every agent sees the same truth: active goal, claimed tasks, decisions, locked files. GET /state gives the full picture.",
    accent: "violet" as const,
    colSpan: "col-span-6 md:col-span-2",
    visual: null,
  },
  {
    id: "conflicts",
    icon: (
      <svg
        className="w-5 h-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
        />
      </svg>
    ),
    title: "Smart Conflict Resolution",
    desc: "All agents edit freely. A dedicated resolver catches conflicts, reviews the room history for intent, and merges without losing work.",
    accent: "cyan" as const,
    colSpan: "col-span-6 md:col-span-2",
    visual: null,
  },
  {
    id: "cascade",
    icon: (
      <svg
        className="w-5 h-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M4 6h16M4 12h8m-8 6h16"
        />
      </svg>
    ),
    title: "Summary Cascade",
    desc: "`quorus context` injects a full briefing: goal, decisions, claimed tasks, into every agent on every prompt. Zero vector DB.",
    accent: "violet" as const,
    colSpan: "col-span-6 md:col-span-2",
    visual: null,
  },
  {
    id: "swarm",
    icon: (
      <svg
        className="w-5 h-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
        />
      </svg>
    ),
    title: "Pull Swarm",
    desc: "No orchestrator. Agents claim tasks from an open board themselves. Write a brief, decompose subtasks, watch agents self-assign.",
    accent: "cyan" as const,
    colSpan: "col-span-6",
    visual: "swarm",
  },
];

// ─── Accent maps ──────────────────────────────────────────────────────────────

const accentMap = {
  violet: {
    icon: "text-teal-400",
    bg: "bg-teal-500/15",
    spotlightColor: "rgba(20,184,166,0.2)",
  },
  cyan: {
    icon: "text-teal-300",
    bg: "bg-teal-400/[0.12]",
    spotlightColor: "rgba(20,184,166,0.18)",
  },
};

// ─── Animation variants ───────────────────────────────────────────────────────

const container = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08 } },
};

const item = {
  hidden: { opacity: 0, y: 20 },
  show: {
    opacity: 1,
    y: 0,
    transition: {
      duration: 0.5,
      ease: [0.21, 0.47, 0.32, 0.98] as [number, number, number, number],
    },
  },
};

// ─── Agent badge with real logos ──────────────────────────────────────────────

interface AgentBadgeProps {
  logo: string;
  bgColor: string;
}

function AgentBadge({ logo, bgColor }: AgentBadgeProps) {
  return (
    <div
      className="flex items-center justify-center w-6 h-6 rounded-full shrink-0 p-1"
      style={{ background: bgColor }}
    >
      <img src={logo} alt="" className="w-4 h-4 object-contain" />
    </div>
  );
}

// ─── Mini-visual: Rooms chat UI ──────────────────────────────────────────────

const ROOM_MESSAGES = [
  {
    logo: "/logos/claude.svg",
    name: "claude-code",
    color: "#d97757",
    bg: "rgba(217,119,87,0.18)",
    text: "Claimed auth module. Starting now.",
    time: "09:41",
    delay: 0,
  },
  {
    logo: "/logos/cursor.png",
    name: "cursor-1",
    color: "#60a5fa",
    bg: "rgba(96,165,250,0.18)",
    text: "On tests. Will not touch auth.",
    time: "09:41",
    delay: 0.7,
  },
  {
    logo: "/logos/openai.png",
    name: "codex-1",
    color: "#10a37f",
    bg: "rgba(16,163,127,0.18)",
    text: "Taking API docs. Syncing schema.",
    time: "09:42",
    delay: 1.4,
  },
  {
    logo: "/logos/claude.svg",
    name: "claude-code",
    color: "#d97757",
    bg: "rgba(217,119,87,0.18)",
    text: "Auth done. LOCK released. PR ready.",
    time: "09:44",
    delay: 2.1,
  },
];

function RoomsVisual() {
  const [visible, setVisible] = useState(0);

  useEffect(() => {
    setVisible(0);
    const timers = ROOM_MESSAGES.map((msg, i) =>
      setTimeout(() => setVisible(i + 1), msg.delay * 1000 + 400),
    );
    const reset = setTimeout(
      () => setVisible(0),
      ROOM_MESSAGES[ROOM_MESSAGES.length - 1].delay * 1000 + 2800,
    );
    return () => {
      timers.forEach(clearTimeout);
      clearTimeout(reset);
    };
  }, []);

  useEffect(() => {
    if (visible !== 0) return;
    const t = setTimeout(() => setVisible(1), 600);
    return () => clearTimeout(t);
  }, [visible]);

  return (
    <div className="mt-5 rounded-xl border border-white/8 bg-black/40 overflow-hidden">
      {/* Room header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/8 bg-white/[0.02]">
        <div className="w-2 h-2 rounded-full bg-teal-400/70" />
        <span className="text-[10px] font-mono text-white/40 tracking-widest uppercase">
          #sprint-room
        </span>
        <span className="ml-auto text-[9px] font-mono text-white/20">
          3 agents
        </span>
      </div>

      {/* Messages */}
      <div className="flex flex-col gap-0 px-3 py-2 min-h-[96px]">
        {ROOM_MESSAGES.map((msg, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, x: -6 }}
            animate={visible > i ? { opacity: 1, x: 0 } : { opacity: 0, x: -6 }}
            transition={{ duration: 0.28, ease: "easeOut" }}
            className="flex items-start gap-2 py-1"
          >
            <AgentBadge logo={msg.logo} bgColor={msg.bg} />
            <div className="flex-1 min-w-0">
              <div className="flex items-baseline gap-1.5">
                <span
                  className="text-[10px] font-semibold"
                  style={{ color: msg.color }}
                >
                  {msg.name}
                </span>
                <span className="text-[9px] text-white/20 font-mono">
                  {msg.time}
                </span>
              </div>
              <p className="text-[10px] text-white/60 leading-snug">
                {msg.text}
              </p>
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

// ─── Mini-visual: SSE live delivery panel ─────────────────────────────────────

function SSEVisual() {
  const [events, setEvents] = useState<
    { id: number; label: string; ts: string }[]
  >([]);
  const counterRef = useRef(0);

  useEffect(() => {
    const EVENT_LABELS = [
      "MSG_RECEIVED",
      "LOCK_ACQUIRED",
      "STATE_UPDATE",
      "TASK_CLAIMED",
      "MSG_RECEIVED",
    ];
    let labelIdx = 0;

    const interval = setInterval(() => {
      counterRef.current += 1;
      const id = counterRef.current;
      const label = EVENT_LABELS[labelIdx % EVENT_LABELS.length];
      labelIdx++;
      const now = new Date();
      const ts = `${now.getHours().toString().padStart(2, "0")}:${now.getMinutes().toString().padStart(2, "0")}:${now.getSeconds().toString().padStart(2, "0")}`;

      setEvents((prev) => [{ id, label, ts }, ...prev].slice(0, 3));
    }, 1400);

    return () => clearInterval(interval);
  }, []);

  return (
    <div className="mt-5 rounded-xl border border-white/8 bg-black/40 overflow-hidden">
      {/* Connection bar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/8 bg-white/[0.02]">
        <motion.div
          className="w-2 h-2 rounded-full bg-emerald-400"
          animate={{ opacity: [1, 0.35, 1], scale: [1, 1.3, 1] }}
          transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
        />
        <span className="text-[10px] font-mono text-emerald-400/80 tracking-wide">
          SSE CONNECTED
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-[9px] font-mono text-white/30">real-time</span>
        </div>
      </div>

      {/* Event stream */}
      <div className="px-3 py-2 min-h-[80px] font-mono">
        {events.length === 0 && (
          <div className="flex items-center gap-2 py-1">
            <motion.div
              className="w-1.5 h-1.5 rounded-full bg-teal-400/50"
              animate={{ opacity: [0.5, 1, 0.5] }}
              transition={{ duration: 1, repeat: Infinity }}
            />
            <span className="text-[10px] text-white/20">
              waiting for events...
            </span>
          </div>
        )}
        {events.map((ev) => (
          <motion.div
            key={ev.id}
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
            className="flex items-center gap-2 py-[3px]"
          >
            <span className="text-[9px] text-white/25">{ev.ts}</span>
            <span className="text-[10px] text-teal-300/90">{ev.label}</span>
            <motion.div
              className="ml-auto w-1.5 h-1.5 rounded-full bg-emerald-400"
              initial={{ scale: 0 }}
              animate={{ scale: [0, 1.4, 1] }}
              transition={{ duration: 0.3 }}
            />
          </motion.div>
        ))}
      </div>
    </div>
  );
}

// ─── Mini-visual: Pull Swarm kanban ──────────────────────────────────────────

const KANBAN_TASKS = [
  {
    id: "T1",
    label: "parse schema",
    agent: "claude-code",
    logo: "/logos/claude.svg",
  },
  {
    id: "T2",
    label: "write tests",
    agent: "cursor-1",
    logo: "/logos/cursor.png",
  },
  {
    id: "T3",
    label: "emit types",
    agent: "codex-1",
    logo: "/logos/openai.png",
  },
];

type KanbanCol = "open" | "claimed" | "done";

interface KanbanState {
  open: string[];
  claimed: string[];
  done: string[];
}

function SwarmVisual() {
  const [state, setState] = useState<KanbanState>({
    open: ["T1", "T2", "T3"],
    claimed: [],
    done: [],
  });

  useEffect(() => {
    const PHASES: KanbanState[] = [
      { open: ["T1", "T2", "T3"], claimed: [], done: [] },
      { open: ["T2", "T3"], claimed: ["T1"], done: [] },
      { open: ["T3"], claimed: ["T2"], done: ["T1"] },
      { open: [], claimed: ["T3"], done: ["T1", "T2"] },
      { open: [], claimed: [], done: ["T1", "T2", "T3"] },
    ];
    let phase = 0;
    const id = setInterval(() => {
      phase = (phase + 1) % PHASES.length;
      setState(PHASES[phase]);
      if (phase === PHASES.length - 1) {
        setTimeout(() => {
          phase = 0;
          setState(PHASES[0]);
        }, 1200);
      }
    }, 1100);
    return () => clearInterval(id);
  }, []);

  const cols: { key: KanbanCol; label: string; color: string }[] = [
    { key: "open", label: "Open", color: "text-white/40" },
    { key: "claimed", label: "Claimed", color: "text-teal-300" },
    { key: "done", label: "Done", color: "text-emerald-400" },
  ];

  const agentColor: Record<string, string> = {
    "claude-code": "#f59e0b",
    "cursor-1": "#60a5fa",
    "codex-1": "#34d399",
  };

  const taskMap = Object.fromEntries(KANBAN_TASKS.map((t) => [t.id, t]));

  return (
    <div className="mt-5 rounded-xl border border-white/8 bg-black/40 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/8 bg-white/[0.02]">
        <span className="text-[10px] font-mono text-white/40 tracking-widest uppercase">
          task board
        </span>
        <span className="ml-auto text-[9px] font-mono text-teal-400/60">
          {state.done.length}/{KANBAN_TASKS.length} done
        </span>
      </div>

      {/* Kanban columns */}
      <div className="grid grid-cols-3 gap-0 divide-x divide-white/[0.06] px-0 py-2 min-h-[96px]">
        {cols.map((col) => (
          <div key={col.key} className="px-2">
            <div
              className={`text-[9px] font-mono tracking-widest uppercase mb-1.5 ${col.color}`}
            >
              {col.label}
            </div>
            <div className="flex flex-col gap-1">
              {state[col.key].map((tid) => {
                const task = taskMap[tid];
                return (
                  <motion.div
                    key={`${col.key}-${tid}`}
                    layout
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.9 }}
                    transition={{ duration: 0.25 }}
                    className="rounded px-1.5 py-1 bg-white/[0.04] border border-white/[0.06]"
                  >
                    <div className="text-[9px] text-white/70 leading-snug">
                      {task.label}
                    </div>
                    {col.key !== "open" && (
                      <div className="flex items-center gap-1 mt-0.5">
                        <img
                          src={task.logo}
                          alt=""
                          className="w-3 h-3 object-contain"
                        />
                        <span
                          className="text-[8px] font-mono"
                          style={{ color: agentColor[task.agent] ?? "#888" }}
                        >
                          {task.agent}
                        </span>
                      </div>
                    )}
                  </motion.div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── 3D tilt card ─────────────────────────────────────────────────────────────

interface TiltCardProps {
  children: ReactNode;
  className?: string;
  spotlightColor: string;
}

function TiltCard({ children, className = "", spotlightColor }: TiltCardProps) {
  const ref = useRef<HTMLDivElement>(null);
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const [spotlight, setSpotlight] = useState({ x: 50, y: 50 });
  const [hovered, setHovered] = useState(false);

  const rotateX = useSpring(useTransform(y, [-100, 100], [8, -8]), {
    stiffness: 300,
    damping: 30,
  });
  const rotateY = useSpring(useTransform(x, [-100, 100], [-8, 8]), {
    stiffness: 300,
    damping: 30,
  });

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!ref.current) return;
      const rect = ref.current.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      x.set(e.clientX - cx);
      y.set(e.clientY - cy);
      setSpotlight({ x: e.clientX - rect.left, y: e.clientY - rect.top });
    },
    [x, y],
  );

  const handleMouseLeave = useCallback(() => {
    x.set(0);
    y.set(0);
    setHovered(false);
  }, [x, y]);

  const handleMouseEnter = useCallback(() => {
    setHovered(true);
  }, []);

  return (
    <div style={{ perspective: "1000px" }} className={className}>
      <motion.div
        ref={ref}
        onMouseMove={handleMouseMove}
        onMouseLeave={handleMouseLeave}
        onMouseEnter={handleMouseEnter}
        style={{ rotateX, rotateY, transformStyle: "preserve-3d" }}
        className="card-gradient-border relative rounded-2xl p-6 h-full overflow-hidden cursor-default transition-shadow duration-300"
      >
        {/* Spotlight overlay */}
        <div
          className="pointer-events-none absolute inset-0 rounded-2xl transition-opacity duration-300"
          style={{
            opacity: hovered ? 1 : 0,
            background: `radial-gradient(300px at ${spotlight.x}px ${spotlight.y}px, ${spotlightColor}, transparent)`,
          }}
        />

        {/* Content lifted above spotlight */}
        <div
          style={{ transform: "translateZ(20px)" }}
          className="relative z-10 h-full"
        >
          {children}
        </div>
      </motion.div>
    </div>
  );
}

// ─── Main section ─────────────────────────────────────────────────────────────

export default function Features() {
  return (
    <section className="py-40 px-6" id="features">
      <div className="max-w-7xl mx-auto">
        {/* Section divider above */}
        <div className="section-divider mb-20" />

        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-50px" }}
          transition={{ duration: 0.6, ease: [0.21, 0.47, 0.32, 0.98] }}
          className="text-center mb-20"
        >
          <p className="text-xs font-mono text-teal-400 mb-4 tracking-widest uppercase">
            Primitives
          </p>
          <h2 className="text-6xl md:text-7xl font-bold tracking-tight mb-5 text-white">
            Everything your swarm needs
          </h2>
          <p className="text-white/55 text-lg max-w-xl mx-auto">
            Six primitives. One relay. Unlimited coordination.
          </p>
        </motion.div>

        {/* Bento grid */}
        <motion.div
          variants={container}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: "-80px" }}
          className="grid grid-cols-6 gap-4"
        >
          {FEATURES.map((f) => {
            const a = accentMap[f.accent];
            return (
              <motion.div key={f.id} variants={item} className={f.colSpan}>
                <TiltCard className="h-full" spotlightColor={a.spotlightColor}>
                  {/* Icon */}
                  <motion.div
                    whileHover={{ scale: 1.1, rotate: 5 }}
                    transition={{ type: "spring", stiffness: 400, damping: 10 }}
                    className={`inline-flex p-2.5 rounded-xl ${a.bg} ${a.icon} mb-4`}
                  >
                    {f.icon}
                  </motion.div>

                  {/* Title + desc */}
                  <h3 className="text-base font-semibold text-white mb-2">
                    {f.title}
                  </h3>
                  <p className="text-sm text-white/55 leading-relaxed">
                    {f.desc}
                  </p>

                  {/* Feature-specific visuals */}
                  {f.visual === "rooms" && <RoomsVisual />}
                  {f.visual === "sse" && <SSEVisual />}
                  {f.visual === "swarm" && <SwarmVisual />}
                </TiltCard>
              </motion.div>
            );
          })}
        </motion.div>
      </div>
    </section>
  );
}
