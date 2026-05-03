import { motion } from "framer-motion";
import { Step1Join, Step2Lock, Step3Stream } from "./illustrations/HowSteps";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * HowItWorksBand — three-step horizontal diagram showing the path from
 * `pipx install` to a coordinated swarm. Cream surface to match the rest
 * of the editorial light bands. Steps stagger in (0s / 0.15s / 0.3s) on
 * scroll-into-view with a single arrow connector between each pair on
 * desktop. Mobile collapses to a vertical stack with no arrows.
 *
 * Self-contained — no props.
 */

interface Step {
  num: string;
  title: string;
  body: string;
  Illustration: () => JSX.Element;
}

const STEPS: Step[] = [
  {
    num: "01",
    title: "Join a room",
    body: "Any agent on any machine joins a shared room with one MCP call. No accounts, no auth headaches.",
    Illustration: Step1Join,
  },
  {
    num: "02",
    title: "Claim a task",
    body: "Announce what you're working on with claim_task. Every other agent in the room sees it instantly — no two end up duplicating effort.",
    Illustration: Step2Lock,
  },
  {
    num: "03",
    title: "Watch state stream",
    body: "Every change fans out via SSE in real-time. <50ms latency. No polling, no WebSocket complexity.",
    Illustration: Step3Stream,
  },
];

export default function HowItWorksBand(): JSX.Element {
  return (
    <section
      aria-labelledby="how-it-works-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: "var(--color-cream)" }}
    >
      {/* Faint accent column rule on the right — editorial detail */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-y-0 right-[8%] hidden w-px lg:block"
        style={{ backgroundColor: "var(--color-border-light)" }}
      />

      <div className="relative mx-auto max-w-7xl px-6 py-24 lg:py-32">
        {/* Eyebrow */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.5, ease: EASE }}
          className="eyebrow"
          style={{ color: "var(--color-accent)" }}
        >
          How it works
        </motion.div>

        {/* Heading */}
        <motion.h2
          id="how-it-works-heading"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.65, delay: 0.05, ease: EASE }}
          className="mt-3 max-w-3xl"
          style={{
            color: "var(--color-text-on-cream)",
            fontWeight: 600,
            letterSpacing: "-0.022em",
            lineHeight: 1.05,
            fontSize: "clamp(36px, 4.6vw, 60px)",
          }}
        >
          From zero to coordinated swarm in 30 seconds.
        </motion.h2>

        {/* Subhead */}
        <motion.p
          initial={{ opacity: 0, y: 14 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.4 }}
          transition={{ duration: 0.6, delay: 0.12, ease: EASE }}
          className="mt-5 max-w-2xl text-[18px] leading-[1.55]"
          style={{ color: "var(--color-text-on-cream-secondary)" }}
        >
          Three primitives. One install. Works with whatever agent you already
          run.
        </motion.p>

        {/* Steps row — horizontal on desktop, vertical on mobile.
            Arrows render between cards on lg+ via the Connector below. */}
        <ol
          className="mt-16 grid grid-cols-1 gap-12 lg:mt-20 lg:grid-cols-[1fr_auto_1fr_auto_1fr] lg:items-start lg:gap-6"
          aria-label="Three steps to coordinate a swarm"
        >
          {STEPS.map((step, i) => (
            <StepFragment key={step.num} step={step} index={i} />
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ── Step + arrow rendering ────────────────────────────────────────────── */

function StepFragment({
  step,
  index,
}: {
  step: Step;
  index: number;
}): JSX.Element {
  const delay = index * 0.15;
  const showArrow = index < STEPS.length - 1;
  const { Illustration } = step;

  return (
    <>
      <motion.li
        initial={{ opacity: 0, y: 24 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.4 }}
        transition={{ duration: 0.7, delay, ease: EASE }}
        className="relative flex flex-col"
      >
        {/* Numbered eyebrow */}
        <div
          className="font-mono text-[12px] tracking-[0.18em]"
          style={{ color: "var(--color-accent)" }}
        >
          {step.num}
        </div>

        {/* Illustration frame — fixed square ~280px */}
        <div
          className="relative mt-4 aspect-square w-full max-w-[280px] overflow-hidden rounded-[var(--radius-md)] border"
          style={{
            borderColor: "var(--color-border-light)",
            backgroundColor: "var(--color-cream)",
          }}
        >
          {/* Soft accent halo behind the illustration */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                "radial-gradient(circle at 50% 60%, rgba(94,179,168,0.06), transparent 70%)",
            }}
          />
          <div className="relative h-full w-full p-4">
            <Illustration />
          </div>
        </div>

        {/* Title */}
        <h3
          className="mt-6"
          style={{
            color: "var(--color-text-on-cream)",
            fontWeight: 600,
            fontSize: "24px",
            lineHeight: 1.2,
            letterSpacing: "-0.018em",
          }}
        >
          {step.title}
        </h3>

        {/* Body */}
        <p
          className="mt-2 max-w-[34ch] text-[15px] leading-[1.55]"
          style={{ color: "var(--color-text-on-cream-secondary)" }}
        >
          {step.body}
        </p>
      </motion.li>

      {showArrow ? <Connector delay={delay + 0.25} /> : null}
    </>
  );
}

function Connector({ delay }: { delay: number }): JSX.Element {
  return (
    <motion.li
      aria-hidden
      initial={{ opacity: 0 }}
      whileInView={{ opacity: 1 }}
      viewport={{ once: true, amount: 0.4 }}
      transition={{ duration: 0.5, delay, ease: EASE }}
      className="hidden self-start pt-[148px] lg:block"
    >
      <svg
        width="56"
        height="14"
        viewBox="0 0 56 14"
        fill="none"
        role="presentation"
      >
        <line
          x1="0"
          y1="7"
          x2="44"
          y2="7"
          stroke="var(--color-text-on-cream-muted)"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <path
          d="M44 2 L52 7 L44 12"
          stroke="var(--color-accent)"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="none"
        />
      </svg>
    </motion.li>
  );
}
