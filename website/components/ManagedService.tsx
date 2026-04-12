"use client";

import { motion } from "framer-motion";
import { useState, FormEvent } from "react";

// ─── Plan data ────────────────────────────────────────────────────────────────

interface Plan {
  name: string;
  price: string;
  period?: string;
  description: string;
  features: string[];
  cta: string;
  accent: "white" | "violet" | "cyan";
  highlight?: boolean;
}

const PLANS: Plan[] = [
  {
    name: "Free",
    price: "Self-hosted",
    description:
      "Run the relay yourself. MIT licensed, unlimited agents, no ceiling.",
    features: [
      "Unlimited agents",
      "Unlimited messages",
      "Rooms + SSE push",
      "Shared state & mutex",
      "MIT license",
      "Community support",
    ],
    cta: "Get started →",
    accent: "white",
  },
  {
    name: "Pro",
    price: "$49",
    period: "/mo",
    description: "Managed relay on relay.murmur.dev — zero infra, just agents.",
    features: [
      "Up to 10 concurrent agents",
      "99.9% uptime SLA",
      "Managed relay — no ops",
      "Web dashboard",
      "Email + chat support",
      "Early access features",
    ],
    cta: "Join waitlist",
    accent: "violet",
    highlight: true,
  },
  {
    name: "Enterprise",
    price: "Custom",
    description:
      "Dedicated infrastructure, compliance, and white-glove support.",
    features: [
      "Unlimited agents",
      "Dedicated relay cluster",
      "Audit logs",
      "SSO / SAML",
      "99.99% SLA",
      "Dedicated Slack channel",
    ],
    cta: "Contact us",
    accent: "cyan",
  },
];

// ─── Animation variants ───────────────────────────────────────────────────────

const container = {
  hidden: {},
  show: { transition: { staggerChildren: 0.1 } },
};

const item = {
  hidden: { opacity: 0, y: 24 },
  show: {
    opacity: 1,
    y: 0,
    transition: {
      duration: 0.55,
      ease: [0.21, 0.47, 0.32, 0.98] as [number, number, number, number],
    },
  },
};

// ─── Check icon ───────────────────────────────────────────────────────────────

function CheckIcon({ accent }: { accent: Plan["accent"] }) {
  const color =
    accent === "violet"
      ? "text-violet-400"
      : accent === "cyan"
        ? "text-cyan-400"
        : "text-white/50";

  return (
    <svg
      className={`w-4 h-4 flex-shrink-0 mt-0.5 ${color}`}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M5 13l4 4L19 7"
      />
    </svg>
  );
}

// ─── Plan card ────────────────────────────────────────────────────────────────

function PlanCard({ plan }: { plan: Plan }) {
  const borderClass = plan.highlight
    ? "border border-violet-500/50 bg-gradient-to-b from-violet-950/30 to-black/60 shadow-lg shadow-violet-500/10"
    : "card-gradient-border";

  return (
    <motion.div variants={item} className="flex flex-col h-full">
      <div
        className={`relative flex flex-col h-full rounded-2xl p-8 ${borderClass} overflow-hidden`}
      >
        {/* Highlight glow */}
        {plan.highlight && (
          <div className="pointer-events-none absolute -top-16 left-1/2 -translate-x-1/2 w-48 h-48 bg-violet-600/20 blur-[80px] rounded-full" />
        )}

        {/* Popular badge */}
        {plan.highlight && (
          <div className="absolute top-4 right-4 px-2.5 py-1 rounded-full bg-violet-500/20 border border-violet-500/30 text-[10px] font-mono text-violet-300 tracking-wider uppercase">
            Most popular
          </div>
        )}

        {/* Plan name */}
        <p className="text-xs font-mono text-white/40 uppercase tracking-widest mb-4">
          {plan.name}
        </p>

        {/* Price */}
        <div className="flex items-end gap-1 mb-2">
          <span className="text-4xl font-bold tracking-tight text-white">
            {plan.price}
          </span>
          {plan.period && (
            <span className="text-white/40 text-sm mb-1">{plan.period}</span>
          )}
        </div>

        {/* Description */}
        <p className="text-sm text-white/40 leading-relaxed mb-8 min-h-[48px]">
          {plan.description}
        </p>

        {/* Feature list */}
        <ul className="flex flex-col gap-3 mb-8 flex-1">
          {plan.features.map((f) => (
            <li
              key={f}
              className="flex items-start gap-2.5 text-sm text-white/60"
            >
              <CheckIcon accent={plan.accent} />
              {f}
            </li>
          ))}
        </ul>

        {/* CTA */}
        <a
          href="#waitlist"
          className={
            plan.highlight
              ? "block text-center px-6 py-3 rounded-full bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-all duration-200 hover:shadow-lg hover:shadow-violet-500/25"
              : "block text-center px-6 py-3 rounded-full border border-white/10 hover:border-white/20 bg-white/5 hover:bg-white/8 text-white text-sm font-medium transition-all duration-200"
          }
        >
          {plan.cta}
        </a>
      </div>
    </motion.div>
  );
}

// ─── Waitlist form ────────────────────────────────────────────────────────────

function WaitlistForm() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!email.trim()) return;
    setLoading(true);
    // Simulate a brief submit delay — no backend needed for now.
    setTimeout(() => {
      setLoading(false);
      setSubmitted(true);
    }, 600);
  };

  if (submitted) {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4, ease: [0.21, 0.47, 0.32, 0.98] }}
        className="flex flex-col items-center gap-3 py-6"
      >
        <div className="w-10 h-10 rounded-full bg-violet-500/20 border border-violet-500/40 flex items-center justify-center">
          <svg
            className="w-5 h-5 text-violet-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M5 13l4 4L19 7"
            />
          </svg>
        </div>
        <p className="text-white font-medium">You&apos;re on the list.</p>
        <p className="text-sm text-white/40">
          We&apos;ll reach out before relay.murmur.dev goes live.
        </p>
      </motion.div>
    );
  }

  return (
    <form
      id="waitlist"
      onSubmit={handleSubmit}
      className="flex flex-col sm:flex-row gap-3 w-full max-w-md mx-auto"
    >
      <input
        type="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="you@example.com"
        className="flex-1 px-4 py-3 rounded-full bg-white/5 border border-white/10 text-white text-sm placeholder:text-white/30 outline-none focus:border-violet-500/60 focus:ring-2 focus:ring-violet-500/20 transition-all duration-200"
      />
      <button
        type="submit"
        disabled={loading}
        className="px-6 py-3 rounded-full bg-violet-600 hover:bg-violet-500 disabled:opacity-60 disabled:cursor-not-allowed text-white text-sm font-medium transition-all duration-200 hover:shadow-lg hover:shadow-violet-500/25 whitespace-nowrap"
      >
        {loading ? "Joining…" : "Join waitlist"}
      </button>
    </form>
  );
}

// ─── Main section ─────────────────────────────────────────────────────────────

export default function ManagedService() {
  return (
    <section className="py-32 px-6 relative overflow-hidden" id="hosted">
      {/* Background elements */}
      <div className="absolute inset-0 grid-bg opacity-20" />
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[400px] bg-violet-600/6 blur-[140px] rounded-full pointer-events-none" />

      <div className="relative max-w-6xl mx-auto">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-50px" }}
          transition={{ duration: 0.6, ease: [0.21, 0.47, 0.32, 0.98] }}
          className="text-center mb-16"
        >
          {/* Badge */}
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-cyan-500/20 bg-cyan-500/10 text-xs text-cyan-300 mb-8 font-mono">
            <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 pulse-dot" />
            Coming soon — launching after April 20 open source release
          </div>

          <h2 className="text-4xl md:text-5xl font-bold tracking-tight mb-5 gradient-text">
            Hosted relay — zero ops
          </h2>
          <p className="text-white/40 text-lg max-w-2xl mx-auto leading-relaxed">
            We&apos;re building the managed version of Murmur. Self-host today
            for free, or get early access to relay.murmur.dev — no servers, no
            config, just agents.
          </p>
        </motion.div>

        {/* Plan cards */}
        <motion.div
          variants={container}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: "-80px" }}
          className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-20"
        >
          {PLANS.map((plan) => (
            <PlanCard key={plan.name} plan={plan} />
          ))}
        </motion.div>

        {/* Waitlist block */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-50px" }}
          transition={{ duration: 0.6, ease: [0.21, 0.47, 0.32, 0.98] }}
          className="card-gradient-border rounded-2xl p-10 text-center max-w-2xl mx-auto"
        >
          <p className="text-xs font-mono text-violet-400 uppercase tracking-widest mb-3">
            Early access
          </p>
          <h3 className="text-2xl font-bold text-white mb-3">
            Get notified when we launch
          </h3>
          <p className="text-sm text-white/40 mb-8 leading-relaxed">
            We&apos;re shipping relay.murmur.dev for teams who don&apos;t want
            to run infrastructure. Join the waitlist and we&apos;ll give you
            first access.
          </p>
          <WaitlistForm />
        </motion.div>
      </div>
    </section>
  );
}
