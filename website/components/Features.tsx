"use client";

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
    desc: "Create shared coordination spaces. Send once — every member receives instantly. N agents, one message, zero duplication.",
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
    desc: "Zero polling. Server-sent events push messages the instant they land. p50 latency of 3.6ms. Your agents never wait.",
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
    id: "mutex",
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
          d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
        />
      </svg>
    ),
    title: "Distributed Mutex Locks",
    desc: "POST /lock to claim a file. TTL auto-expires. SSE broadcasts LOCK_ACQUIRED to the room. No conflicts. No lost work.",
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
    desc: "`murmur context` injects a full briefing — goal, decisions, claimed tasks — into every agent on every prompt. Zero vector DB.",
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
    icon: "text-violet-400",
    bg: "bg-violet-500/15",
    spotlightColor: "rgba(124,58,237,0.2)",
  },
  cyan: {
    icon: "text-violet-300",
    bg: "bg-violet-400/12",
    spotlightColor: "rgba(139,92,246,0.18)",
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

// ─── Mini-visual: Rooms ───────────────────────────────────────────────────────

function RoomsVisual() {
  return (
    <div className="relative flex items-center justify-center h-20 mt-4">
      {/* Connection lines (SVG behind dots) */}
      <svg
        className="absolute inset-0 w-full h-full"
        viewBox="0 0 200 80"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        {/* Line A→center */}
        <motion.line
          x1="30"
          y1="40"
          x2="100"
          y2="40"
          stroke="rgba(124,58,237,0.5)"
          strokeWidth="1.5"
          strokeDasharray="4 3"
          animate={{ strokeDashoffset: [0, -14] }}
          transition={{ duration: 0.8, repeat: Infinity, ease: "linear" }}
        />
        {/* Line center→B */}
        <motion.line
          x1="100"
          y1="40"
          x2="170"
          y2="18"
          stroke="rgba(124,58,237,0.5)"
          strokeWidth="1.5"
          strokeDasharray="4 3"
          animate={{ strokeDashoffset: [0, -14] }}
          transition={{
            duration: 0.8,
            repeat: Infinity,
            ease: "linear",
            delay: 0.15,
          }}
        />
        {/* Line center→C */}
        <motion.line
          x1="100"
          y1="40"
          x2="170"
          y2="62"
          stroke="rgba(124,58,237,0.5)"
          strokeWidth="1.5"
          strokeDasharray="4 3"
          animate={{ strokeDashoffset: [0, -14] }}
          transition={{
            duration: 0.8,
            repeat: Infinity,
            ease: "linear",
            delay: 0.3,
          }}
        />
      </svg>

      {/* Agent A */}
      <motion.div
        className="absolute flex items-center justify-center w-8 h-8 rounded-full bg-violet-500/20 border border-violet-500/40 text-violet-400 text-[10px] font-mono"
        style={{ left: "10%", top: "50%", transform: "translateY(-50%)" }}
        animate={{ scale: [1, 1.08, 1] }}
        transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
      >
        A
      </motion.div>

      {/* Hub */}
      <motion.div
        className="absolute flex items-center justify-center w-10 h-10 rounded-full bg-violet-600/30 border border-violet-500/60 text-violet-300 text-[10px] font-mono"
        style={{ left: "50%", top: "50%", transform: "translate(-50%, -50%)" }}
        animate={{
          scale: [1, 1.06, 1],
          boxShadow: [
            "0 0 0px rgba(124,58,237,0.3)",
            "0 0 16px rgba(124,58,237,0.5)",
            "0 0 0px rgba(124,58,237,0.3)",
          ],
        }}
        transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
      >
        hub
      </motion.div>

      {/* Agent B */}
      <motion.div
        className="absolute flex items-center justify-center w-8 h-8 rounded-full bg-violet-500/20 border border-violet-500/40 text-violet-400 text-[10px] font-mono"
        style={{ right: "10%", top: "18%" }}
        animate={{ scale: [1, 1.08, 1] }}
        transition={{
          duration: 2,
          repeat: Infinity,
          ease: "easeInOut",
          delay: 0.4,
        }}
      >
        B
      </motion.div>

      {/* Agent C */}
      <motion.div
        className="absolute flex items-center justify-center w-8 h-8 rounded-full bg-violet-500/20 border border-violet-500/40 text-violet-400 text-[10px] font-mono"
        style={{ right: "10%", bottom: "18%" }}
        animate={{ scale: [1, 1.08, 1] }}
        transition={{
          duration: 2,
          repeat: Infinity,
          ease: "easeInOut",
          delay: 0.8,
        }}
      >
        C
      </motion.div>
    </div>
  );
}

// ─── Mini-visual: SSE latency counter ────────────────────────────────────────

function SSEVisual() {
  const [ms, setMs] = useState(3.6);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    const interval = setInterval(() => {
      const next = parseFloat((Math.random() * 4 + 2).toFixed(1));
      setMs(next);
      setFlash(true);
      setTimeout(() => setFlash(false), 220);
    }, 1400);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex items-center gap-3 mt-4">
      {/* Indicator dot */}
      <motion.div
        className="w-2 h-2 rounded-full bg-violet-400"
        animate={{ opacity: [1, 0.3, 1] }}
        transition={{ duration: 1, repeat: Infinity, ease: "easeInOut" }}
      />
      <div className="font-mono text-sm text-white/50">p50 latency</div>
      <motion.div
        key={ms}
        className="font-mono font-bold text-lg tabular-nums"
        style={{ color: flash ? "#a78bfa" : "#c4b5fd" }}
        animate={flash ? { scale: [1, 1.25, 1] } : { scale: 1 }}
        transition={{ duration: 0.22 }}
      >
        {ms}ms
      </motion.div>
    </div>
  );
}

// ─── Mini-visual: Pull Swarm task board ──────────────────────────────────────

const TASK_ROWS = [
  { label: "parse schema", agent: "agent-3" },
  { label: "write tests", agent: "agent-1" },
  { label: "emit types", agent: "agent-7" },
];

type TaskStatus = "open" | "claimed" | "done";

function SwarmVisual() {
  const [statuses, setStatuses] = useState<TaskStatus[]>([
    "open",
    "open",
    "open",
  ]);

  useEffect(() => {
    let step = 0;
    const tick = () => {
      step++;
      if (step % 3 === 1) {
        // Claim row 0
        setStatuses(["claimed", "open", "open"]);
      } else if (step % 3 === 2) {
        // Claim row 1, finish row 0
        setStatuses(["done", "claimed", "open"]);
      } else {
        // Claim row 2, finish others, reset
        setStatuses(["open", "done", "claimed"]);
        setTimeout(() => setStatuses(["open", "open", "open"]), 900);
      }
    };
    const id = setInterval(tick, 1200);
    return () => clearInterval(id);
  }, []);

  const statusColor: Record<TaskStatus, string> = {
    open: "text-white/40 border-white/10",
    claimed: "text-violet-300 border-violet-500/40",
    done: "text-violet-400 border-violet-500/40",
  };

  const statusLabel: Record<TaskStatus, string> = {
    open: "open",
    claimed: "claimed",
    done: "done",
  };

  return (
    <div className="flex flex-col gap-2 mt-4 w-full max-w-xs">
      {TASK_ROWS.map((row, i) => (
        <motion.div
          key={row.label}
          className={`flex items-center justify-between px-3 py-1.5 rounded-lg border text-xs font-mono ${statusColor[statuses[i]]}`}
          layout
          transition={{ duration: 0.25 }}
        >
          <span className="text-white/60">{row.label}</span>
          <div className="flex items-center gap-2">
            {statuses[i] !== "open" && (
              <span className="text-[10px] opacity-60">{row.agent}</span>
            )}
            <motion.span
              key={statuses[i]}
              initial={{ opacity: 0, x: 4 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.2 }}
            >
              {statusLabel[statuses[i]]}
            </motion.span>
          </div>
        </motion.div>
      ))}
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

        {/* Content (lifted above spotlight) */}
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
    <section className="py-32 px-6" id="features">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-50px" }}
          transition={{ duration: 0.6, ease: [0.21, 0.47, 0.32, 0.98] }}
          className="text-center mb-16"
        >
          <p className="text-sm font-mono text-violet-400 mb-3 tracking-widest uppercase">
            Primitives
          </p>
          <h2 className="text-5xl md:text-6xl font-bold tracking-tight mb-4">
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
