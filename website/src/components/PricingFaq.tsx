import { useState } from "react";
import { Link } from "react-router-dom";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";

const COLORS = {
  cream: "#f5f1ea",
  borderLight: "rgba(10,10,15,0.08)",
  textPrimary: "#0a0a0f",
  textSecondary: "#4a4a52",
  accent: "#0d4d4a",
} as const;

const EASE = [0.16, 1, 0.3, 1] as const;
const SANS = "'Plus Jakarta Sans', system-ui, sans-serif";
const MONO = "'JetBrains Mono', ui-monospace, monospace";

interface FaqItem {
  q: string;
  a: string;
}

const FAQ: FaqItem[] = [
  {
    q: "Self-hosted vs hosted — which should I pick?",
    a: "Both ship the same primitives. Self-hosted is one Python process you run on Fly, Railway, or your own box — free forever, MIT-licensed, and the right call when your data can't leave your perimeter. The hosted relay is what you reach for when you'd rather not babysit a process and want SLAs, residency controls, and SSO out of the box.",
  },
  {
    q: "Where does my data live?",
    a: "On Free, the public relay runs in US East. On Pro, you choose US or EU at room creation and we never replicate across regions. On Enterprise, we deploy a dedicated relay in any cloud region you specify — including private VPC peering. Quorus only ever stores room state, message bodies, and lock metadata; no model weights, no PII unless you put it there.",
  },
  {
    q: "Am I locked into a specific model?",
    a: "No. Quorus is the coordination layer beneath your models — agents bring their own model and the relay doesn't care whether they're Claude, GPT, Gemini, Llama, or a script. Switch model providers any time without touching room code.",
  },
  {
    q: "Can I bring my own API keys?",
    a: "Yes. BYOK is on every tier. Quorus never proxies model calls — your agents talk directly to whichever provider you've configured, so spend stays on your account and we never see the keys.",
  },
  {
    q: "How do upgrades and downgrades work?",
    a: "Upgrade any time and pay the prorated difference on your next invoice. Downgrade takes effect at the end of the current billing period — your data stays put, you just stop having access to the higher-tier features. No retention games.",
  },
  {
    q: "What's the refund policy?",
    a: "If the hosted relay misses its SLA in any month, you get service credits applied automatically. If you cancel within the first 30 days of a paid plan and weren't getting value, email us and we'll refund the full month — one click, no friction.",
  },
];

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <motion.svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden
      animate={{ rotate: open ? 180 : 0 }}
      transition={{ duration: 0.3, ease: EASE }}
    >
      <path
        d="M4 6l4 4 4-4"
        stroke={COLORS.textSecondary}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </motion.svg>
  );
}

function FaqRow({ item, index }: { item: FaqItem; index: number }) {
  const [open, setOpen] = useState(false);
  const prefersReduced = useReducedMotion();

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ duration: 0.5, delay: index * 0.04, ease: EASE }}
      style={{ borderBottom: `1px solid ${COLORS.borderLight}` }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-6 py-5 text-left"
        style={{
          background: "transparent",
          border: "none",
          cursor: "pointer",
        }}
      >
        <span
          className="text-[16px] md:text-[17px]"
          style={{
            color: COLORS.textPrimary,
            fontFamily: SANS,
            fontWeight: 500,
            letterSpacing: "-0.01em",
          }}
        >
          {item.q}
        </span>
        <ChevronIcon open={open} />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="content"
            initial={prefersReduced ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={prefersReduced ? undefined : { height: 0, opacity: 0 }}
            transition={{ duration: 0.35, ease: EASE }}
            style={{ overflow: "hidden" }}
          >
            <p
              className="pb-6 pr-10 text-[14.5px] leading-[1.65]"
              style={{ color: COLORS.textSecondary, fontFamily: SANS }}
            >
              {item.a}
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

export default function PricingFaq() {
  return (
    <section
      aria-labelledby="faq-heading"
      className="relative w-full"
      style={{ backgroundColor: COLORS.cream }}
    >
      <div className="mx-auto max-w-3xl px-6 py-24 md:py-32">
        <div className="mb-12 text-center">
          <motion.p
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.3 }}
            transition={{ duration: 0.6, ease: EASE }}
            className="text-[11px] uppercase"
            style={{
              color: COLORS.accent,
              fontFamily: MONO,
              letterSpacing: "0.22em",
            }}
          >
            FAQ
          </motion.p>
          <motion.h2
            id="faq-heading"
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.3 }}
            transition={{ duration: 0.6, ease: EASE, delay: 0.05 }}
            className="mt-4 text-balance"
            style={{
              color: COLORS.textPrimary,
              fontFamily: SANS,
              fontSize: "clamp(36px, 4.5vw, 56px)",
              fontWeight: 600,
              lineHeight: 1.05,
              letterSpacing: "-0.022em",
            }}
          >
            Questions
          </motion.h2>
        </div>

        <div style={{ borderTop: `1px solid ${COLORS.borderLight}` }}>
          {FAQ.map((item, i) => (
            <FaqRow key={item.q} item={item} index={i} />
          ))}
        </div>

        <p
          className="mt-10 text-center text-[13px]"
          style={{ color: COLORS.textSecondary, fontFamily: SANS }}
        >
          Still wondering?{" "}
          <Link
            to="/docs"
            style={{
              color: COLORS.accent,
              fontFamily: MONO,
              fontSize: 13,
            }}
          >
            Read the docs
          </Link>{" "}
          or{" "}
          <a
            href="mailto:hi@quorus.dev"
            style={{
              color: COLORS.accent,
              fontFamily: MONO,
              fontSize: 13,
            }}
          >
            email us
          </a>
          .
        </p>
      </div>
    </section>
  );
}
