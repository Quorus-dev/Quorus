import { motion } from "framer-motion";

const ITEMS = [
  { name: "Claude Code", dot: "bg-amber-400" },
  { name: "Cursor", dot: "bg-blue-400" },
  { name: "OpenAI Codex", dot: "bg-green-400" },
  { name: "Gemini", dot: "bg-indigo-400" },
  { name: "Ollama", dot: "bg-orange-400" },
  { name: "Antigravity", dot: "bg-pink-400" },
  { name: "Open Interpreter", dot: "bg-emerald-400" },
  { name: "Any HTTP client", dot: "bg-white/40" },
];

// Triple for seamless loop
const TRACK = [...ITEMS, ...ITEMS, ...ITEMS];

function Pill({ name, dot }: { name: string; dot: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full border border-gray-200 bg-white text-xs text-gray-600 font-medium whitespace-nowrap shrink-0 shadow-sm">
      <span className={`w-1.5 h-1.5 rounded-full ${dot} opacity-90`} />
      {name}
    </span>
  );
}

export default function SocialProof() {
  return (
    <section className="relative py-12 overflow-hidden bg-[#faf9f7]">
      {/* Edge fades */}
      <div
        className="absolute inset-y-0 left-0 w-28 pointer-events-none z-10"
        style={{
          background: "linear-gradient(90deg, #faf9f7 0%, transparent 100%)",
        }}
      />
      <div
        className="absolute inset-y-0 right-0 w-28 pointer-events-none z-10"
        style={{
          background: "linear-gradient(270deg, #faf9f7 0%, transparent 100%)",
        }}
      />

      {/* Divider lines */}
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-gray-300/50 to-transparent" />
      <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-gray-300/50 to-transparent" />

      {/* Label */}
      <p className="text-center text-[10px] text-gray-400 font-mono tracking-[0.2em] uppercase mb-6">
        Works with every AI agent
      </p>

      {/* Marquee track */}
      <div className="relative flex overflow-hidden">
        <motion.div
          className="flex gap-3"
          animate={{ x: ["0%", "-33.333%"] }}
          transition={{
            duration: 28,
            repeat: Infinity,
            ease: "linear",
          }}
        >
          {TRACK.map((item, i) => (
            <Pill key={`${item.name}-${i}`} name={item.name} dot={item.dot} />
          ))}
        </motion.div>
      </div>
    </section>
  );
}
