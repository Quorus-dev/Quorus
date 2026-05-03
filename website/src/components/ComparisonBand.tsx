import { motion } from "framer-motion";
import ComparisonTable from "./ComparisonTable";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * ComparisonBand — cream surface that frames the Quorus vs LangGraph vs CrewAI
 * comparison matrix. Eyebrow + headline + subhead, then the table.
 *
 * Self-contained: no props. Composes with ComparisonTable.
 */
export default function ComparisonBand() {
  return (
    <section
      aria-labelledby="comparison-band-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: "var(--color-cream)" }}
    >
      {/* Soft accent wash, lower-right — same family as HeroLight */}
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-32 -right-32 h-[480px] w-[480px] rounded-full"
        style={{
          background:
            "radial-gradient(circle at 70% 30%, rgba(13,77,74,0.04), transparent 60%)",
        }}
      />

      <div className="relative mx-auto max-w-7xl px-6 py-24 lg:py-32">
        <div className="max-w-3xl">
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.5, ease: EASE }}
            className="eyebrow"
            style={{ color: "var(--color-accent)" }}
          >
            How we compare
          </motion.div>

          <motion.h2
            id="comparison-band-heading"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.65, delay: 0.05, ease: EASE }}
            className="mt-3"
            style={{
              color: "var(--color-text-on-cream)",
              fontWeight: 600,
              letterSpacing: "-0.022em",
              lineHeight: 1.05,
              fontSize: "clamp(36px, 4.6vw, 60px)",
            }}
          >
            Cross-vendor coordination, not just orchestration.
          </motion.h2>

          <motion.p
            initial={{ opacity: 0, y: 14 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.6, delay: 0.12, ease: EASE }}
            className="mt-5 max-w-2xl text-[18px] leading-[1.55]"
            style={{ color: "var(--color-text-on-cream-secondary)" }}
          >
            Other tools build for one model. Quorus glues them all together —
            and ships with shared state, task claims, and SSE out of the box.
          </motion.p>
        </div>

        <ComparisonTable />
      </div>
    </section>
  );
}
