import { motion } from "framer-motion";

const TESTIMONIALS = [
  {
    quote:
      "We run Claude Code, Cursor, and Codex agents in parallel on every PR. Quorus is the only thing that keeps them from stepping on each other.",
    name: "Maya Chen",
    role: "Staff Engineer",
    tag: "private beta",
    avatar: "MC",
    color: "violet",
  },
  {
    quote:
      "Set up in 8 minutes. Three agents coordinating file locks, sharing task state, broadcasting decisions. It just works. No YAML, no ops.",
    name: "Ravi Patel",
    role: "AI Platform Lead",
    tag: "private beta",
    avatar: "RP",
    color: "cyan",
  },
  {
    quote:
      "SSE push is the real unlock. No polling. Messages arrive in under 5ms and my Gemini + Claude swarm stays in sync across machines.",
    name: "Sofia Larsson",
    role: "Systems Architect",
    tag: "early access",
    avatar: "SL",
    color: "violet",
  },
];

export default function Testimonials() {
  return (
    <section className="py-24 px-6">
      <div className="max-w-6xl mx-auto">
        <motion.div
          className="text-center mb-14"
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.55 }}
        >
          <p className="text-xs font-mono text-white/25 tracking-widest uppercase mb-3">
            Early access
          </p>
          <h2 className="text-3xl md:text-4xl font-bold tracking-tight text-white">
            What early teams are saying
          </h2>
        </motion.div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          {TESTIMONIALS.map((t, i) => (
            <motion.div
              key={t.name}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: i * 0.1 }}
              className="card-gradient-border rounded-2xl p-6 flex flex-col gap-5"
            >
              {/* Stars */}
              <div className="flex gap-0.5">
                {Array.from({ length: 5 }).map((_, j) => (
                  <svg
                    key={j}
                    className="w-3.5 h-3.5 text-teal-400/70"
                    fill="currentColor"
                    viewBox="0 0 20 20"
                  >
                    <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                  </svg>
                ))}
              </div>

              <p className="text-white/55 text-sm leading-relaxed flex-1">
                &ldquo;{t.quote}&rdquo;
              </p>

              <div className="flex items-center gap-3 pt-3 border-t border-white/[0.06]">
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold font-mono shrink-0 ${
                    t.color === "violet"
                      ? "bg-teal-500/20 text-teal-300"
                      : "bg-cyan-500/20 text-cyan-300"
                  }`}
                >
                  {t.avatar}
                </div>
                <div className="min-w-0">
                  <div className="text-sm font-medium text-white/80 truncate">
                    {t.name}
                  </div>
                  <div className="text-xs text-white/30 truncate">
                    {t.role} <span className="text-white/15">·</span>{" "}
                    <span className="text-teal-400/60">{t.tag}</span>
                  </div>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
