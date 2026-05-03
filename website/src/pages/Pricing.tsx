import { useEffect } from "react";
import { motion } from "framer-motion";
import CTADark from "../components/CTADark";
import FooterV2 from "../components/FooterV2";
import PricingTable from "../components/PricingTable";
import PricingFaq from "../components/PricingFaq";

const COLORS = {
  cream: "#f5f1ea",
  ink: "#0a0a0f",
  borderLight: "rgba(10,10,15,0.08)",
  borderLightStrong: "rgba(10,10,15,0.12)",
  textPrimary: "#0a0a0f",
  textSecondary: "#4a4a52",
  textMuted: "#7a7a82",
  accent: "#0d4d4a",
} as const;

const EASE = [0.16, 1, 0.3, 1] as const;
const SANS = "'Plus Jakarta Sans', system-ui, sans-serif";
const MONO = "'JetBrains Mono', ui-monospace, monospace";

interface Tier {
  name: string;
  eyebrow: string;
  price: string;
  cadence: string;
  tagline: string;
  cta: { label: string; href: string; external?: boolean };
  features: string[];
  recommended?: boolean;
}

const TIERS: Tier[] = [
  {
    name: "Free",
    eyebrow: "INDIVIDUAL",
    price: "$0",
    cadence: "forever",
    tagline: "For solo builders running a couple of agents on the side.",
    cta: {
      label: "Start free",
      href: "https://github.com/Quorus-dev/Quorus",
      external: true,
    },
    features: [
      "3 active rooms",
      "5 agents per room",
      "Public relay (quorus.dev)",
      "All 12 MCP coordination tools",
      "Distributed locks + shared state",
      "Community support on Discord",
    ],
  },
  {
    name: "Pro",
    eyebrow: "RECOMMENDED",
    price: "$20",
    cadence: "/ user / month",
    tagline:
      "For working engineers running real swarms across Claude, Cursor, and Codex.",
    cta: {
      label: "Join the waitlist",
      href: "mailto:hi@quorus.dev?subject=Pro%20waitlist",
    },
    features: [
      "50 active rooms",
      "25 agents per room",
      "99.9% SSE delivery SLA",
      "30-day audit log + replay",
      "Workspace admin + roles",
      "US / EU data residency",
      "Email support · 1-day response",
    ],
    recommended: true,
  },
  {
    name: "Enterprise",
    eyebrow: "TEAM",
    price: "Custom",
    cadence: "annual",
    tagline:
      "For platform teams shipping coordinated agents in regulated environments.",
    cta: {
      label: "Talk to us",
      href: "mailto:hi@quorus.dev?subject=Enterprise",
    },
    features: [
      "Unlimited rooms + agents",
      "Dedicated relay, your region",
      "SSO (SAML / OIDC) + SCIM",
      "99.99% delivery SLA",
      "Custom MSA, DPA, BAA",
      "Onboarding + dedicated channel",
    ],
  },
];

/* ── Tiny SVG icons — no Lucide, no emoji ─────────────────────────────────── */

function CheckIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden
      style={{ flexShrink: 0, marginTop: 4 }}
    >
      <path
        d="M3.5 8.5l3 3 6-6"
        stroke={COLORS.accent}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ArrowIcon({ color }: { color: string }) {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden
      style={{ marginLeft: 4 }}
    >
      <path
        d="M3 9l6-6M9 3H4.5M9 3v4.5"
        stroke={color}
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/* ── Hero ─────────────────────────────────────────────────────────────────── */

function Hero() {
  return (
    <section
      aria-labelledby="pricing-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: COLORS.cream }}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute -top-32 -right-32 h-[480px] w-[480px] rounded-full"
        style={{
          background:
            "radial-gradient(circle at 70% 30%, rgba(13,77,74,0.05), transparent 60%)",
        }}
      />
      <div className="relative mx-auto max-w-4xl px-6 pt-32 pb-20 text-center md:pt-40 md:pb-24">
        <motion.p
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: EASE }}
          className="text-[11px] uppercase"
          style={{
            color: COLORS.accent,
            fontFamily: MONO,
            letterSpacing: "0.22em",
          }}
        >
          Pricing
        </motion.p>
        <motion.h1
          id="pricing-heading"
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.05, ease: EASE }}
          className="mt-5 text-balance"
          style={{
            color: COLORS.textPrimary,
            fontFamily: SANS,
            fontWeight: 600,
            letterSpacing: "-0.022em",
            lineHeight: 0.98,
            fontSize: "clamp(44px, 6vw, 76px)",
          }}
        >
          Build with the relay.
          <br />
          Pay when you&apos;re real.
        </motion.h1>
        <motion.p
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.15, ease: EASE }}
          className="mx-auto mt-6 max-w-xl text-pretty"
          style={{
            color: COLORS.textSecondary,
            fontFamily: SANS,
            fontSize: 18,
            lineHeight: 1.55,
          }}
        >
          Quorus is open source and free to self-host. The hosted relay scales
          with your team — same primitives, more shoulders.
        </motion.p>
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.25, ease: EASE }}
          className="mt-7"
        >
          <a
            href="https://github.com/Quorus-dev/Quorus"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center text-[13px] transition-colors duration-200"
            style={{ color: COLORS.accent, fontFamily: MONO }}
            onMouseEnter={(e) =>
              (e.currentTarget.style.color = COLORS.textPrimary)
            }
            onMouseLeave={(e) => (e.currentTarget.style.color = COLORS.accent)}
          >
            View open-source repo
            <ArrowIcon color="currentColor" />
          </a>
        </motion.div>
      </div>
    </section>
  );
}

/* ── Tier cards ───────────────────────────────────────────────────────────── */

function CtaButton({
  href,
  external,
  emphasis,
  children,
}: {
  href: string;
  external?: boolean;
  emphasis?: boolean;
  children: React.ReactNode;
}) {
  const sharedStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    height: 44,
    width: "100%",
    borderRadius: 12,
    fontFamily: SANS,
    fontSize: 14,
    fontWeight: 500,
    letterSpacing: "-0.005em",
    transition:
      "transform 200ms cubic-bezier(0.16,1,0.3,1), background-color 200ms",
    marginTop: 28,
  };

  const emphasisStyle: React.CSSProperties = emphasis
    ? { backgroundColor: COLORS.ink, color: COLORS.cream }
    : {
        backgroundColor: "transparent",
        color: COLORS.textPrimary,
        border: `1px solid ${COLORS.borderLightStrong}`,
      };

  const onMouseEnter = (e: React.MouseEvent<HTMLAnchorElement>) => {
    e.currentTarget.style.transform = "translateY(-1px)";
    if (!emphasis) {
      e.currentTarget.style.backgroundColor = "rgba(10,10,15,0.04)";
    }
  };
  const onMouseLeave = (e: React.MouseEvent<HTMLAnchorElement>) => {
    e.currentTarget.style.transform = "translateY(0)";
    if (!emphasis) {
      e.currentTarget.style.backgroundColor = "transparent";
    }
  };

  if (external) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        style={{ ...sharedStyle, ...emphasisStyle }}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
      >
        {children}
      </a>
    );
  }
  return (
    <a
      href={href}
      style={{ ...sharedStyle, ...emphasisStyle }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {children}
    </a>
  );
}

function TierCard({ tier, index }: { tier: Tier; index: number }) {
  const recommended = !!tier.recommended;
  const isExternal = !!tier.cta.external;

  return (
    <motion.article
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ duration: 0.6, delay: index * 0.08, ease: EASE }}
      className="relative flex flex-col"
      style={{
        backgroundColor: recommended
          ? "rgba(13,77,74,0.025)"
          : "rgba(255,255,255,0.45)",
        border: `1px solid ${
          recommended ? "rgba(13,77,74,0.35)" : COLORS.borderLight
        }`,
        borderRadius: 12,
        padding: recommended ? "36px 28px" : "28px",
        marginTop: recommended ? -12 : 0,
        boxShadow: recommended ? "0 12px 32px rgba(13,77,74,0.08)" : "none",
      }}
    >
      <div className="flex items-center justify-between">
        <p
          className="text-[11px] uppercase"
          style={{
            color: recommended ? COLORS.accent : COLORS.textMuted,
            fontFamily: MONO,
            letterSpacing: "0.22em",
          }}
        >
          {tier.eyebrow}
        </p>
        {recommended && (
          <span
            className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] uppercase"
            style={{
              color: COLORS.cream,
              backgroundColor: COLORS.accent,
              fontFamily: MONO,
              letterSpacing: "0.18em",
            }}
          >
            Most teams
          </span>
        )}
      </div>

      <h3
        className="mt-5"
        style={{
          color: COLORS.textPrimary,
          fontFamily: SANS,
          fontSize: 28,
          fontWeight: 600,
          letterSpacing: "-0.022em",
          lineHeight: 1.1,
        }}
      >
        {tier.name}
      </h3>

      <p
        className="mt-2 text-[14px] leading-[1.55]"
        style={{ color: COLORS.textSecondary, fontFamily: SANS }}
      >
        {tier.tagline}
      </p>

      <div className="mt-7 flex items-baseline gap-2">
        <span
          style={{
            color: COLORS.textPrimary,
            fontFamily: SANS,
            fontSize: 44,
            fontWeight: 600,
            letterSpacing: "-0.022em",
            fontVariantNumeric: "tabular-nums",
            lineHeight: 1,
          }}
        >
          {tier.price}
        </span>
        <span
          className="text-[13px]"
          style={{ color: COLORS.textMuted, fontFamily: MONO }}
        >
          {tier.cadence}
        </span>
      </div>

      <CtaButton
        href={tier.cta.href}
        external={isExternal}
        emphasis={recommended}
      >
        {tier.cta.label}
      </CtaButton>

      <ul className="mt-7 flex flex-1 flex-col">
        {tier.features.map((f, idx) => (
          <li
            key={f}
            className="flex items-start gap-3 py-3"
            style={{
              borderTop: idx === 0 ? "none" : `1px solid ${COLORS.borderLight}`,
            }}
          >
            <CheckIcon />
            <span
              className="text-[13.5px] leading-[1.5]"
              style={{ color: COLORS.textSecondary, fontFamily: SANS }}
            >
              {f}
            </span>
          </li>
        ))}
      </ul>
    </motion.article>
  );
}

function TierGrid() {
  return (
    <section
      aria-labelledby="tiers-heading"
      className="relative w-full"
      style={{ backgroundColor: COLORS.cream }}
    >
      <h2 id="tiers-heading" className="sr-only">
        Pricing tiers
      </h2>
      <div className="mx-auto max-w-7xl px-6 pb-24 md:pb-32">
        <div className="grid grid-cols-1 items-start gap-5 md:grid-cols-3 md:gap-6">
          {TIERS.map((tier, i) => (
            <TierCard key={tier.name} tier={tier} index={i} />
          ))}
        </div>
        <p
          className="mt-10 text-center text-[12px]"
          style={{
            color: COLORS.textMuted,
            fontFamily: MONO,
            letterSpacing: "0.04em",
          }}
        >
          All prices in USD · cancel any time · no card required to start
        </p>
      </div>
    </section>
  );
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function Pricing() {
  useEffect(() => {
    const prevTitle = document.title;
    document.title = "Pricing — Quorus";
    const desc = document.querySelector('meta[name="description"]');
    const prevDesc = desc?.getAttribute("content") ?? null;
    desc?.setAttribute(
      "content",
      "Quorus pricing — open source and free to self-host. Hosted Free, Pro at $20/user/mo, and Enterprise with SSO and dedicated relay.",
    );
    return () => {
      document.title = prevTitle;
      if (prevDesc !== null) desc?.setAttribute("content", prevDesc);
    };
  }, []);

  return (
    <main
      id="main"
      className="min-h-screen"
      style={{ backgroundColor: COLORS.cream }}
    >
      <Hero />
      <TierGrid />
      <PricingTable />
      <PricingFaq />
      <CTADark />
      <FooterV2 />
    </main>
  );
}
