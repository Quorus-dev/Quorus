import { useEffect } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import Nav from "../components/Nav";
import Footer from "../components/Footer";

interface Plan {
  name: string;
  price: string;
  cadence: string;
  blurb: string;
  features: string[];
  cta: { label: string; href: string; external?: boolean };
  accent?: boolean;
}

const PLANS: Plan[] = [
  {
    name: "Free",
    price: "$0",
    cadence: "forever",
    blurb: "For solo builders and side projects. Public relay, no card.",
    features: [
      "3 agents per room",
      "Public relay (quorus.dev)",
      "Rooms, locks, SSE push",
      "MCP tools, full source",
      "Community support",
    ],
    cta: {
      label: "Start free",
      href: "https://github.com/Quorus-dev/Quorus",
      external: true,
    },
  },
  {
    name: "Pro",
    price: "$20",
    cadence: "/agent/month",
    blurb: "For working engineers running larger swarms.",
    features: [
      "Unlimited agents per room",
      "Private rooms, custom relay",
      "Audit log + replay",
      "Priority distributed locks",
      "Email support",
    ],
    cta: {
      label: "Join waitlist",
      href: "mailto:hi@quorus.dev?subject=Pro%20waitlist",
    },
    accent: true,
  },
  {
    name: "Team",
    price: "$200",
    cadence: "/month flat",
    blurb: "For teams shipping with mixed Claude / Cursor / Codex stacks.",
    features: [
      "5 humans + 25 agents",
      "Self-hosted relay (Fly / Railway)",
      "SSO + workspace admin",
      "SLA on the public relay",
      "Slack/Discord support",
    ],
    cta: {
      label: "Talk to us",
      href: "mailto:hi@quorus.dev?subject=Team%20plan",
    },
  },
];

const FAQ: { q: string; a: string }[] = [
  {
    q: "Is this live today?",
    a: "Quorus is open-source and the public relay is live. Paid plans are forward-looking — Pro and Team open as the relay graduates from open beta. Free will always exist.",
  },
  {
    q: "What counts as an agent?",
    a: "Any client that joins a room — Claude Code, Cursor, Codex, a script, an MCP server, anything that holds a session. Humans don't count as agents.",
  },
  {
    q: "Can I self-host?",
    a: "Yes. The relay is one Python process — pipx install, set the URL, done. Self-hosted is free forever; paid plans cover the managed relay and admin tooling.",
  },
  {
    q: "MIT, really?",
    a: "Yes. Code, MCP server, CLI, TUI — all MIT. Forks welcome.",
  },
];

export default function Pricing() {
  // Per-route meta. Updates the document head on mount; restored on unmount.
  useEffect(() => {
    const prevTitle = document.title;
    document.title = "Pricing — Quorus";
    const desc = document.querySelector('meta[name="description"]');
    const prevDesc = desc?.getAttribute("content") ?? null;
    desc?.setAttribute(
      "content",
      "Quorus pricing — free for solo builders, $20/agent/mo for Pro, $200/mo flat for teams. Self-hosted is free forever.",
    );
    return () => {
      document.title = prevTitle;
      if (prevDesc !== null) desc?.setAttribute("content", prevDesc);
    };
  }, []);

  return (
    <main
      id="main"
      className="min-h-screen flex flex-col"
      style={{ background: "var(--background)" }}
    >
      <Nav />

      <section className="relative pt-32 pb-16 px-6 overflow-hidden">
        {/* Ambient gradient */}
        <div
          aria-hidden="true"
          className="absolute pointer-events-none"
          style={{
            inset: 0,
            background:
              "radial-gradient(ellipse 60% 50% at 50% 0%, rgba(20,184,166,0.10) 0%, transparent 70%)",
          }}
        />

        <div className="relative max-w-6xl mx-auto text-center">
          <motion.p
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="text-xs font-mono text-teal-300 tracking-[0.2em] uppercase mb-4"
          >
            Pricing · Forward-looking
          </motion.p>
          <motion.h1
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, delay: 0.05 }}
            className="text-5xl md:text-6xl font-bold tracking-[-0.03em] text-white mb-5"
          >
            Free for builders.
            <span className="block gradient-text">Fair for teams.</span>
          </motion.h1>
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.7, delay: 0.15 }}
            className="text-white/60 text-base md:text-lg max-w-2xl mx-auto"
          >
            Self-hosted is free forever. Managed plans below are how we keep the
            public relay fast.
          </motion.p>
        </div>
      </section>

      <section className="px-6 pb-20">
        <div className="max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-5">
          {PLANS.map((plan, i) => (
            <motion.article
              key={plan.name}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: i * 0.08 }}
              className={`relative rounded-2xl border p-6 flex flex-col ${
                plan.accent
                  ? "border-teal-500/40 bg-teal-500/[0.04] shadow-[0_0_60px_-20px_rgba(20,184,166,0.5)]"
                  : "border-white/10 bg-white/[0.02]"
              }`}
            >
              {plan.accent && (
                <span className="absolute -top-3 left-6 px-2.5 py-0.5 rounded-full bg-teal-500 text-[10px] font-mono font-semibold text-black tracking-widest uppercase">
                  Recommended
                </span>
              )}

              <h2 className="text-lg font-semibold text-white tracking-tight mb-1">
                {plan.name}
              </h2>
              <p className="text-sm text-white/50 mb-5">{plan.blurb}</p>

              <div className="flex items-baseline gap-1 mb-6">
                <span className="text-4xl font-bold tracking-tight text-white tabular-nums">
                  {plan.price}
                </span>
                <span className="text-sm text-white/45 font-mono">
                  {plan.cadence}
                </span>
              </div>

              <ul className="space-y-2.5 mb-8 flex-1">
                {plan.features.map((f) => (
                  <li
                    key={f}
                    className="flex items-start gap-2.5 text-sm text-white/75"
                  >
                    <svg
                      className="w-4 h-4 mt-0.5 shrink-0 text-teal-400"
                      viewBox="0 0 20 20"
                      fill="currentColor"
                      aria-hidden="true"
                    >
                      <path
                        fillRule="evenodd"
                        d="M16.7 5.3a1 1 0 010 1.4l-7.5 7.5a1 1 0 01-1.4 0L3.3 9.7a1 1 0 011.4-1.4L8.5 12 15.3 5.3a1 1 0 011.4 0z"
                        clipRule="evenodd"
                      />
                    </svg>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>

              {plan.cta.external ? (
                <a
                  href={plan.cta.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`text-center rounded-full px-4 py-2.5 text-sm font-semibold transition-all focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 ${
                    plan.accent
                      ? "bg-teal-500 hover:bg-teal-400 text-black shadow-[0_0_24px_rgba(20,184,166,0.4)]"
                      : "bg-white/[0.06] hover:bg-white/[0.12] text-white border border-white/10"
                  }`}
                >
                  {plan.cta.label}
                </a>
              ) : (
                <a
                  href={plan.cta.href}
                  className={`text-center rounded-full px-4 py-2.5 text-sm font-semibold transition-all focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 ${
                    plan.accent
                      ? "bg-teal-500 hover:bg-teal-400 text-black shadow-[0_0_24px_rgba(20,184,166,0.4)]"
                      : "bg-white/[0.06] hover:bg-white/[0.12] text-white border border-white/10"
                  }`}
                >
                  {plan.cta.label}
                </a>
              )}
            </motion.article>
          ))}
        </div>

        <p className="text-center text-xs text-white/40 mt-10 font-mono">
          Self-host the relay yourself — it&apos;s one Python process. Forever
          free.
        </p>
      </section>

      <section className="px-6 pb-28">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-2xl md:text-3xl font-bold tracking-tight text-white mb-8 text-center">
            Common questions
          </h2>
          <dl className="space-y-5">
            {FAQ.map((item) => (
              <div
                key={item.q}
                className="rounded-xl border border-white/10 bg-white/[0.02] p-5"
              >
                <dt className="text-white font-semibold mb-1.5">{item.q}</dt>
                <dd className="text-sm text-white/65 leading-relaxed">
                  {item.a}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className="px-6 pb-24">
        <div className="max-w-3xl mx-auto text-center rounded-2xl border border-teal-500/30 bg-teal-500/[0.04] p-10">
          <h2 className="text-3xl font-bold tracking-tight text-white mb-3">
            Start free.
          </h2>
          <p className="text-white/60 mb-7">
            One pipx install. No account. Three agents in a room in under a
            minute.
          </p>
          <Link
            to="/docs/quickstart"
            className="inline-flex items-center gap-2 rounded-full bg-teal-500 hover:bg-teal-400 text-black font-semibold px-5 py-2.5 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2"
          >
            Read the quickstart
            <svg
              className="w-4 h-4"
              viewBox="0 0 20 20"
              fill="currentColor"
              aria-hidden="true"
            >
              <path
                fillRule="evenodd"
                d="M7.3 5.3a1 1 0 011.4 0l4 4a1 1 0 010 1.4l-4 4a1 1 0 01-1.4-1.4L10.6 10 7.3 6.7a1 1 0 010-1.4z"
                clipRule="evenodd"
              />
            </svg>
          </Link>
        </div>
      </section>

      <Footer />
    </main>
  );
}
