import { motion, useReducedMotion } from "framer-motion";

/**
 * HeroCoalesce — three vendor chips that flow into one room shape.
 * Single Framer Motion sequence, GPU-accelerated (transform/opacity only),
 * respects prefers-reduced-motion. ~85 LOC including markup.
 */

const VENDORS = [
  { name: "Claude", color: "#d97757", logo: "/logos/claude.svg", x: -180 },
  { name: "Cursor", color: "#60a5fa", logo: "/logos/cursor.png", x: 0 },
  { name: "Codex", color: "#10a37f", logo: "/logos/openai.png", x: 180 },
] as const;

const SEQUENCE_DURATION = 2.6;

export default function HeroCoalesce() {
  const reduce = useReducedMotion();

  return (
    <div
      aria-hidden="true"
      className="relative h-32 w-full max-w-2xl mx-auto select-none pointer-events-none"
    >
      {/* Center room target */}
      <motion.div
        initial={reduce ? { opacity: 1 } : { opacity: 0, scale: 0.6 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{
          duration: 0.7,
          delay: reduce ? 0 : SEQUENCE_DURATION - 0.4,
          ease: [0.16, 1, 0.3, 1],
        }}
        className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
      >
        <div className="relative w-24 h-24 rounded-full border border-teal-400/40 bg-teal-500/[0.06] flex items-center justify-center backdrop-blur-sm">
          <span className="text-[10px] font-mono text-teal-300 tracking-[0.18em] uppercase">
            Room
          </span>
          <span className="absolute inset-0 rounded-full border border-teal-400/30 animate-ping" />
        </div>
      </motion.div>

      {/* Vendor chips */}
      {VENDORS.map((v, i) => (
        <motion.div
          key={v.name}
          initial={
            reduce
              ? { opacity: 1, x: 0, y: 0, scale: 1 }
              : { opacity: 0, x: v.x, y: -40, scale: 0.8 }
          }
          animate={
            reduce
              ? { opacity: 1, x: 0, y: 0, scale: 1 }
              : {
                  opacity: [0, 1, 1, 0.4],
                  x: [v.x, v.x, 0, 0],
                  y: [-40, -10, 0, 0],
                  scale: [0.8, 1, 0.7, 0.5],
                }
          }
          transition={{
            duration: SEQUENCE_DURATION,
            delay: i * 0.12,
            times: [0, 0.25, 0.85, 1],
            ease: [0.16, 1, 0.3, 1],
          }}
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
          style={{ willChange: "transform, opacity" }}
        >
          <div
            className="flex items-center gap-2 px-3 py-1.5 rounded-full border bg-[#0a0a14]/85 backdrop-blur-md font-mono text-xs"
            style={{ borderColor: `${v.color}55`, color: v.color }}
          >
            <img
              src={v.logo}
              alt=""
              width={14}
              height={14}
              className="object-contain"
            />
            <span>{v.name}</span>
          </div>
        </motion.div>
      ))}

      {/* Connecting beams that fade in as chips reach center */}
      {!reduce && (
        <svg
          className="absolute inset-0 w-full h-full pointer-events-none"
          viewBox="0 0 600 128"
          preserveAspectRatio="none"
        >
          {VENDORS.map((v, i) => (
            <motion.line
              key={v.name}
              x1={300 + v.x * 0.8}
              y1={64}
              x2={300}
              y2={64}
              stroke={v.color}
              strokeOpacity="0.4"
              strokeWidth="1"
              strokeDasharray="2 4"
              initial={{ pathLength: 0, opacity: 0 }}
              animate={{ pathLength: [0, 1, 1, 0], opacity: [0, 0.6, 0.6, 0] }}
              transition={{
                duration: SEQUENCE_DURATION,
                delay: i * 0.12 + 0.3,
                times: [0, 0.4, 0.85, 1],
              }}
            />
          ))}
        </svg>
      )}
    </div>
  );
}
