import { motion, useReducedMotion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * BentoStitch — dark band that mirrors the Stitch "Modern Feature Bento Grid"
 * design directly. Renders the cropped Stitch bento image as the centerpiece,
 * with our real React heading above it. The image surface itself is the
 * Stitch composition; we own the framing and the copy around it.
 *
 * Sets data-theme="dark" so the global nav inverts on scroll-over.
 */
export default function BentoStitch() {
  const prefersReduced = useReducedMotion();

  return (
    <section
      data-theme="dark"
      aria-labelledby="bento-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: "var(--color-ink)" }}
    >
      {/* Faint circuit-grid backdrop tone, matching the Stitch image surround */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.35]"
        style={{
          background:
            "radial-gradient(circle at 20% 0%, rgba(94,179,168,0.08), transparent 45%), radial-gradient(circle at 80% 100%, rgba(116,37,244,0.06), transparent 45%)",
        }}
      />

      <div className="relative mx-auto max-w-7xl px-6 py-24 lg:py-32">
        {/* Eyebrow */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.5, ease: EASE }}
          className="eyebrow"
          style={{ color: "var(--color-accent-on-ink)" }}
        >
          The Quorus surface
        </motion.div>

        {/* Heading */}
        <motion.h2
          id="bento-heading"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.65, delay: 0.05, ease: EASE }}
          className="mt-3 max-w-3xl"
          style={{
            color: "var(--color-text-on-ink)",
            fontWeight: 600,
            letterSpacing: "-0.02em",
            lineHeight: 1.02,
            fontSize: "clamp(36px, 4.6vw, 60px)",
          }}
        >
          Everything your swarm needs.
        </motion.h2>

        {/* Subhead */}
        <motion.p
          initial={{ opacity: 0, y: 14 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.6, delay: 0.12, ease: EASE }}
          className="mt-5 max-w-2xl text-[18px] leading-[1.55]"
          style={{ color: "var(--color-text-on-ink-secondary)" }}
        >
          Six primitives. One relay. Unlimited coordination — across Claude,
          Cursor, Codex, Gemini, and anything else you wire up.
        </motion.p>

        {/* The Stitch bento — cropped showcase image */}
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.2 }}
          transition={{ duration: 0.9, delay: 0.2, ease: EASE }}
          className="relative mt-14 overflow-hidden rounded-2xl border"
          style={{
            borderColor: "var(--color-border-dark-strong)",
            boxShadow:
              "0 32px 80px rgba(0,0,0,0.45), 0 0 0 1px rgba(94,179,168,0.05) inset",
          }}
        >
          {/* Soft accent halo wash behind the image */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                "radial-gradient(ellipse at 50% 50%, rgba(94,179,168,0.08), transparent 70%)",
            }}
          />
          <motion.img
            src="/stitch/bento-cards.webp"
            alt="Six bento cards showing the Quorus primitives: SSE Push Delivery, Shared State Matrix, Summary Cascade, Rooms & Fan-out, Smart Conflict Resolution, and Pull Swarm"
            width={1296}
            height={445}
            draggable={false}
            className="relative block h-auto w-full select-none"
            animate={prefersReduced ? undefined : { y: [0, -3, 0] }}
            transition={{ duration: 11, ease: "easeInOut", repeat: Infinity }}
          />
        </motion.div>

        {/* Card legend — names under the image map to what's shown above */}
        <motion.ul
          initial={{ opacity: 0, y: 12 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.3 }}
          transition={{ duration: 0.6, delay: 0.3, ease: EASE }}
          className="mt-10 grid grid-cols-2 gap-x-6 gap-y-3 font-mono text-[12px] sm:grid-cols-3 lg:grid-cols-6"
          style={{ color: "var(--color-text-on-ink-secondary)" }}
        >
          {LEGEND.map((item) => (
            <li key={item.k} className="flex items-baseline gap-2">
              <span
                aria-hidden
                className="inline-block h-1 w-1 flex-shrink-0 rounded-full"
                style={{ backgroundColor: "var(--color-accent-on-ink)" }}
              />
              <span style={{ color: "var(--color-text-on-ink)" }}>
                {item.k}
              </span>
            </li>
          ))}
        </motion.ul>
      </div>
    </section>
  );
}

const LEGEND: Array<{ k: string }> = [
  { k: "SSE Push Delivery" },
  { k: "Shared State Matrix" },
  { k: "Summary Cascade" },
  { k: "Rooms & Fan-out" },
  { k: "Smart Conflict Resolution" },
  { k: "Pull Swarm" },
];
