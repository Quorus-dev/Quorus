
import { motion } from "framer-motion";
import Waitlist from "./Waitlist";

const PROOF = [
  "866+ tests",
  "3.6ms p50",
  "281 msg/s",
  "Any model",
  "Any machine",
];

export default function CTA() {
  return (
    <section id="waitlist" className="relative py-40 px-6 overflow-hidden">
      {/* Deep background layers */}
      <div className="absolute inset-0 grid-bg opacity-30" />

      {/* Converging radial grid — creates a dramatic focal point */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 70% 60% at 50% 100%, rgba(124,58,237,0.12) 0%, transparent 70%)",
        }}
      />

      {/* Multi-layer glow stack */}
      <div
        className="absolute bottom-0 left-1/2 -translate-x-1/2 w-[1000px] h-[500px] rounded-full blur-[140px] pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(124,58,237,0.22) 0%, rgba(109,40,217,0.08) 50%, transparent 70%)",
        }}
      />
      <div
        className="absolute bottom-0 left-1/2 -translate-x-1/2 w-[500px] h-[300px] rounded-full blur-[80px] pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(139,92,246,0.3) 0%, transparent 70%)",
        }}
      />
      <div
        className="absolute bottom-0 left-1/2 -translate-x-1/2 w-[200px] h-[150px] rounded-full blur-[40px] pointer-events-none"
        style={{ background: "rgba(167,139,250,0.25)" }}
      />

      {/* Vignette */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 80% 50% at 50% 50%, transparent 40%, rgba(6,6,10,0.6) 100%)",
        }}
      />

      <div className="relative max-w-2xl mx-auto text-center">
        {/* Badge */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-violet-500/30 bg-violet-500/10 text-xs text-violet-300 mb-10 font-mono"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-violet-400 pulse-dot" />
          Private beta · We review every application
        </motion.div>

        {/* Headline */}
        <motion.h2
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6, delay: 0.08 }}
          className="text-5xl md:text-7xl font-bold tracking-tight leading-[1.02] mb-6"
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
          className="text-white/55 text-lg mb-12 leading-relaxed"
        >
          Murmur gives your AI swarms rooms, shared state, and real-time
          coordination — across any model, any machine. We&apos;re onboarding
          early teams now.
        </motion.p>

        {/* Form — elevated card treatment */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6, delay: 0.24 }}
          className="relative mb-8"
        >
          {/* Card — animated gradient border */}
          <div className="animated-border rounded-2xl bg-[#0a0a1a]/90 backdrop-blur-sm p-6">
            <Waitlist
              size="lg"
              className="max-w-md mx-auto"
              label="Request early access"
            />
            <p className="text-xs text-white/20 mt-4 flex items-center justify-center gap-3">
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
          className="flex flex-wrap items-center justify-center gap-2"
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
    </section>
  );
}
