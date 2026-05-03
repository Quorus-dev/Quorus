import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import BrainSynapses from "./illustrations/BrainSynapses";

const EASE = [0.16, 1, 0.3, 1] as const;
const INSTALL_CMD = "pip install quorus";

/**
 * HeroLight — cream split hero. Left: badge + headline + subhead + waitlist
 * + install command. Right: the Stitch "Hybrid Elite" hero image — a
 * cinematic teal-lit brain with a baked-in orchestrator terminal.
 *
 * Source: /public/stitch/brain-scene.webp (cropped from Stitch screen
 * b94ee1506d6541939266712982a0dfe4).
 */
export default function HeroLight() {
  return (
    <section
      aria-labelledby="hero-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: "var(--color-cream)" }}
    >
      {/* Single subtle radial — accent tint at the lower-left, not the
          AI-template centered halo. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-32 -left-32 h-[480px] w-[480px] rounded-full"
        style={{
          background:
            "radial-gradient(circle at 30% 70%, rgba(13,77,74,0.05), transparent 60%)",
        }}
      />

      {/* Faint vertical column rule — editorial accent */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-y-0 right-[8%] hidden w-px lg:block"
        style={{ backgroundColor: "var(--color-border-light)" }}
      />

      <div className="relative mx-auto grid max-w-7xl grid-cols-1 items-center gap-12 px-6 pb-24 pt-32 lg:grid-cols-12 lg:gap-10 lg:pt-40">
        {/* Left column — copy + CTA */}
        <div className="lg:col-span-6">
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, ease: EASE }}
            className="inline-flex items-center gap-2 rounded-full border px-3 py-1"
            style={{ borderColor: "var(--color-border-light-strong)" }}
          >
            <span
              className="block h-1.5 w-1.5 rounded-full"
              style={{ backgroundColor: "var(--color-accent)" }}
            />
            <span
              className="font-mono text-[11px] tracking-wider"
              style={{ color: "var(--color-text-on-cream-secondary)" }}
            >
              OPEN BETA · v0.4 · MIT
            </span>
          </motion.div>

          <motion.h1
            id="hero-heading"
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.05, ease: EASE }}
            className="mt-7"
            style={{
              color: "var(--color-text-on-cream)",
              fontWeight: 600,
              letterSpacing: "-0.022em",
              lineHeight: 0.98,
              fontSize: "clamp(44px, 6vw, 76px)",
            }}
          >
            Coordination Layer
            <br />
            for Agent Teams
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.15, ease: EASE }}
            className="mt-6 max-w-xl text-[18px] leading-[1.55]"
            style={{ color: "var(--color-text-on-cream-secondary)" }}
          >
            Quorus gives your AI swarms rooms, shared state, and real-time
            coordination.{" "}
            <span style={{ color: "var(--color-text-on-cream)" }}>
              Any model. Any machine.
            </span>
          </motion.p>

          {/* Waitlist row — inline email + CTA, style only */}
          <motion.form
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.25, ease: EASE }}
            onSubmit={(e) => e.preventDefault()}
            className="mt-9 flex w-full max-w-md flex-col gap-2 sm:flex-row"
            aria-label="Join the waitlist"
          >
            <input
              type="email"
              placeholder="you@company.com"
              className="h-11 flex-1 rounded-md border bg-white/60 px-4 text-[14px] outline-none transition-colors placeholder:text-slate-400/80 focus:border-[var(--color-accent)] focus:bg-white"
              style={{
                borderColor: "var(--color-border-light-strong)",
                color: "var(--color-text-on-cream)",
              }}
            />
            <button
              type="submit"
              className="h-11 rounded-md px-5 text-[13px] font-medium tracking-tight transition-transform duration-200 hover:-translate-y-px"
              style={{
                backgroundColor: "var(--color-ink)",
                color: "var(--color-cream)",
              }}
            >
              Join waitlist
            </button>
          </motion.form>

          {/* Install command — copyable */}
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.35, ease: EASE }}
            className="mt-5 max-w-md"
          >
            <InstallCommand />
            <p
              className="mt-2 font-mono text-[11px]"
              style={{ color: "var(--color-text-on-cream-muted)" }}
            >
              Or `quorus init` after install. Python 3.10+. MIT.
            </p>
          </motion.div>
        </div>

        {/* Right column — Stitch brain hero (image asset). The image already
            includes the orchestrator terminal panel, baked into the
            composition. We add only a soft accent halo + subtle float. */}
        <div className="lg:col-span-6">
          <HeroBrain />
        </div>
      </div>
    </section>
  );
}

/* ── Stitch brain — cinematic teal-lit hero asset ────────────────────────── */

function HeroBrain() {
  const prefersReduced = useReducedMotion();

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.96 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 1.1, delay: 0.35, ease: EASE }}
      className="relative mx-auto w-full max-w-[640px] lg:ml-auto lg:mr-0"
    >
      {/* Soft accent halo behind the brain */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -m-8"
        style={{
          background:
            "radial-gradient(circle at 60% 50%, rgba(94,179,168,0.20), rgba(13,77,74,0.05) 40%, transparent 70%)",
          filter: "blur(28px)",
        }}
      />

      {/* Brain image + animated synapse overlay. The image is the static
          Stitch crop (776×700); BrainSynapses is a transparent SVG layer
          positioned over it so the synapses pulse on top of the brain. */}
      <motion.div
        className="relative block w-full"
        animate={prefersReduced ? undefined : { y: [0, -6, 0] }}
        transition={{ duration: 9, ease: "easeInOut", repeat: Infinity }}
      >
        <img
          src="/stitch/brain-scene.webp"
          alt="A glowing teal-lit brain with an orchestrator terminal showing Quorus initializing a multi-model swarm"
          width={776}
          height={700}
          draggable={false}
          className="relative block h-auto w-full select-none"
          loading="eager"
          decoding="async"
        />
        <BrainSynapses />
      </motion.div>
    </motion.div>
  );
}

/* ── Install command ─────────────────────────────────────────────────────── */

function InstallCommand() {
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_CMD);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard may be blocked in some contexts — silently no-op.
    }
  };

  return (
    <div
      className="group flex items-center gap-3 rounded-md border px-4 py-3 transition-colors"
      style={{
        backgroundColor: "var(--color-ink-2)",
        borderColor: "var(--color-border-dark)",
      }}
    >
      <span
        className="font-mono text-[12px]"
        style={{ color: "var(--color-accent-on-ink)" }}
      >
        $
      </span>
      <code
        className="flex-1 overflow-x-auto whitespace-nowrap font-mono text-[12px]"
        style={{ color: "var(--color-text-on-ink)" }}
      >
        {INSTALL_CMD}
      </code>
      <button
        type="button"
        onClick={onCopy}
        aria-label={copied ? "Copied" : "Copy install command"}
        className="rounded px-2 py-1 font-mono text-[11px] transition-colors"
        style={{
          color: copied
            ? "var(--color-accent-on-ink)"
            : "var(--color-text-on-ink-secondary)",
        }}
      >
        {copied ? "copied" : "copy"}
      </button>
    </div>
  );
}
