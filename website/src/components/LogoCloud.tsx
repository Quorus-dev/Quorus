import { motion, useReducedMotion } from "framer-motion";

const COLORS = {
  cream: "#f5f1ea",
  textMuted: "#7a7a82",
  textSecondary: "#4a4a52",
} as const;

const EASE = [0.16, 1, 0.3, 1] as const;

// The contract names six providers. We prefer text-only fallbacks because
// (a) we don't want to show fake brand names like the Stitch mock did, and
// (b) the existing /public/logos/ directory only has a partial overlap with
// the contract list, which would force a confusing mix of marks + text.
// Mono wordmarks set in JetBrains Mono with a center middot keep it distinctly
// "developer tool" rather than "enterprise sales page".
const PROVIDERS = [
  "anthropic",
  "openai",
  "google",
  "mistral",
  "cursor",
  "codex",
] as const;

export default function LogoCloud() {
  const prefersReduced = useReducedMotion();

  return (
    <section
      aria-label="Powers agent swarms across major model providers"
      className="w-full"
      style={{ backgroundColor: COLORS.cream }}
    >
      <div className="mx-auto max-w-6xl px-6 py-14 md:py-16">
        <motion.p
          initial={{ opacity: 0, y: 12 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.3 }}
          transition={{ duration: 0.6, ease: EASE }}
          className="text-center text-[11px] uppercase"
          style={{
            color: COLORS.textMuted,
            fontFamily: "'JetBrains Mono', ui-monospace, monospace",
            letterSpacing: "0.18em",
          }}
        >
          Powers agent swarms across
        </motion.p>

        <motion.ul
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, amount: 0.3 }}
          variants={{
            hidden: {},
            show: {
              transition: { staggerChildren: prefersReduced ? 0 : 0.05 },
            },
          }}
          className="mt-7 flex flex-wrap items-center justify-center gap-x-8 gap-y-4 md:gap-x-12"
        >
          {PROVIDERS.map((name, i) => (
            <motion.li
              key={name}
              variants={{
                hidden: { opacity: 0, y: 8 },
                show: { opacity: 1, y: 0 },
              }}
              transition={{ duration: 0.5, ease: EASE }}
              className="flex items-center gap-x-8 md:gap-x-12"
            >
              <span
                className="select-none text-[15px] tracking-tight transition-colors duration-200"
                style={{
                  color: COLORS.textSecondary,
                  fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                  fontWeight: 500,
                  // Subtle desaturation so the wordmarks read as a band, not a
                  // shouty list of brand names.
                  opacity: 0.7,
                }}
                onMouseEnter={(e) => (e.currentTarget.style.opacity = "1")}
                onMouseLeave={(e) => (e.currentTarget.style.opacity = "0.7")}
              >
                {name}
              </span>
              {i < PROVIDERS.length - 1 && (
                <span
                  aria-hidden
                  className="text-[10px]"
                  style={{ color: COLORS.textMuted, opacity: 0.5 }}
                >
                  ·
                </span>
              )}
            </motion.li>
          ))}
        </motion.ul>
      </div>
    </section>
  );
}
