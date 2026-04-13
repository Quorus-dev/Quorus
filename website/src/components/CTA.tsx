import { motion } from "framer-motion";
import Waitlist from "./Waitlist";
import TerminalAnimation from "./TerminalAnimation";

const PROOF = [
  "11 MCP tools",
  "870+ tests",
  "MIT license",
  "Any model",
  "Any machine",
];

export default function CTA() {
  return (
    <section id="waitlist" className="relative py-32 px-6 overflow-hidden">
      {/* Subtle grid */}
      <div className="absolute inset-0 grid-bg opacity-20" />

      {/* Ambient gradient */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 60% 50% at 50% 100%, rgba(20,184,166,0.1) 0%, transparent 70%)",
        }}
      />

      <div className="relative max-w-6xl mx-auto">
        {/* Two column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 items-center">
          {/* Left: Text and waitlist */}
          <div className="text-center lg:text-left">
            {/* Badge */}
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5 }}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-teal-500/30 bg-teal-500/10 text-xs text-teal-300 mb-8 font-mono"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-teal-400 pulse-dot" />
              Private beta · We review every application
            </motion.div>

            {/* Headline */}
            <motion.h2
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6, delay: 0.08 }}
              className="text-4xl md:text-5xl lg:text-6xl font-bold tracking-tight leading-[1.05] mb-6"
            >
              <span className="text-white">Your agents.</span>
              <br />
              <span className="gradient-text">Finally connected.</span>
            </motion.h2>

            <motion.p
              initial={{ opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6, delay: 0.16 }}
              className="text-white/50 text-lg mb-10 leading-relaxed max-w-md mx-auto lg:mx-0"
            >
              Murmur gives your AI swarms rooms, shared state, and real-time
              coordination. Any model, any machine.
            </motion.p>

            {/* Waitlist form */}
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6, delay: 0.24 }}
              className="mb-8"
            >
              <div className="rounded-2xl bg-[#0a0a1a]/80 backdrop-blur-sm p-5 border border-white/[0.08] max-w-md mx-auto lg:mx-0">
                <Waitlist
                  size="lg"
                  className="w-full"
                  label="Request early access"
                />
                <p className="text-xs text-white/20 mt-3 flex items-center justify-center lg:justify-start gap-3">
                  <span>No spam, ever.</span>
                  <span className="w-px h-3 bg-white/10" />
                  <span>Unsubscribe anytime.</span>
                </p>
              </div>
            </motion.div>

            {/* Proof pills */}
            <motion.div
              initial={{ opacity: 0 }}
              whileInView={{ opacity: 1 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6, delay: 0.36 }}
              className="flex flex-wrap items-center justify-center lg:justify-start gap-2"
            >
              {PROOF.map((p, i) => (
                <motion.span
                  key={p}
                  initial={{ opacity: 0, scale: 0.9 }}
                  whileInView={{ opacity: 1, scale: 1 }}
                  viewport={{ once: true }}
                  transition={{ delay: 0.4 + i * 0.06 }}
                  className="px-3 py-1 rounded-full border border-white/8 bg-white/[0.03] text-xs text-white/30 font-mono"
                >
                  {p}
                </motion.span>
              ))}
            </motion.div>
          </div>

          {/* Right: Murmur TUI preview */}
          <motion.div
            initial={{ opacity: 0, x: 30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.7, delay: 0.2 }}
            className="hidden lg:block"
          >
            <div className="relative">
              {/* Glow behind terminal */}
              <div
                className="absolute -inset-8 pointer-events-none"
                style={{
                  background:
                    "radial-gradient(ellipse at center, rgba(20,184,166,0.15) 0%, transparent 70%)",
                  filter: "blur(30px)",
                }}
              />
              <TerminalAnimation />
            </div>
          </motion.div>
        </div>

        {/* Mobile: show terminal below */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6, delay: 0.4 }}
          className="lg:hidden mt-16"
        >
          <p className="text-center text-[10px] font-mono text-white/25 mb-4 tracking-widest uppercase">
            Murmur TUI preview
          </p>
          <TerminalAnimation />
        </motion.div>
      </div>
    </section>
  );
}
