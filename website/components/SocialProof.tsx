"use client";
import { motion } from "framer-motion";

const WORKS_WITH = [
  { name: "Claude Code", dot: "bg-violet-400" },
  { name: "Cursor", dot: "bg-blue-400" },
  { name: "OpenAI Codex", dot: "bg-green-400" },
  { name: "Gemini", dot: "bg-cyan-400" },
  { name: "Ollama", dot: "bg-orange-400" },
  { name: "Any HTTP client", dot: "bg-white/40" },
];

export default function SocialProof() {
  return (
    <section className="relative py-10 overflow-hidden">
      {/* Top/bottom fade */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "linear-gradient(90deg, rgba(6,6,10,0.8) 0%, transparent 15%, transparent 85%, rgba(6,6,10,0.8) 100%)",
        }}
      />
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/8 to-transparent" />
      <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-white/8 to-transparent" />

      <div className="max-w-7xl mx-auto px-6">
        <div className="flex flex-col sm:flex-row items-center justify-center gap-6 sm:gap-10">
          <span className="text-xs text-white/20 shrink-0 font-mono tracking-widest uppercase">
            Works with
          </span>
          <div className="w-px h-4 bg-white/10 hidden sm:block" />
          <div className="flex flex-wrap items-center justify-center gap-3">
            {WORKS_WITH.map((item, i) => (
              <motion.span
                key={item.name}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.07, duration: 0.4 }}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-white/6 bg-white/[0.025] text-xs text-white/45 hover:text-white/70 hover:border-white/12 transition-all duration-200 font-medium"
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${item.dot} opacity-70`}
                />
                {item.name}
              </motion.span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
