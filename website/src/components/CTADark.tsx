import { useCallback, useState } from "react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";

const COLORS = {
  ink: "#0a0a0f",
  ink2: "#14141c",
  borderDark: "rgba(255,255,255,0.08)",
  textPrimary: "#f5f1ea",
  textSecondary: "#a8a8b0",
  textMuted: "#6a6a72",
  accentOnInk: "#5eb3a8",
} as const;

const EASE = [0.16, 1, 0.3, 1] as const;
const MONO = "'JetBrains Mono', ui-monospace, monospace";
const SANS = "'Plus Jakarta Sans', system-ui, sans-serif";

// 2026-05-16 audit (B7): PyPI serves stale 0.1.0 — `pip install quorus`
// gives the wrong package. Use the git-installed pipx form until PyPI
// republish lands.
const INSTALL_CMD =
  'pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"';

const NOISE_SVG =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'>
      <filter id='n'>
        <feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/>
        <feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.06 0'/>
      </filter>
      <rect width='100%' height='100%' filter='url(#n)'/>
    </svg>`,
  );

function CopyIcon({ color }: { color: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
      <rect
        x="4.5"
        y="4.5"
        width="8"
        height="8"
        rx="1.5"
        stroke={color}
        strokeWidth="1.25"
      />
      <path
        d="M3.5 11V4.5A1.5 1.5 0 0 1 5 3h6.5"
        stroke={color}
        strokeWidth="1.25"
        strokeLinecap="round"
      />
    </svg>
  );
}

function CheckIcon({ color }: { color: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path
        d="M3.5 8.5l3 3 6-6"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ArrowIcon({ color }: { color: string }) {
  return (
    <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden>
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

function CodeBlock() {
  const prefersReduced = useReducedMotion();
  const [copied, setCopied] = useState(false);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_CMD);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard can fail in insecure contexts — silently no-op so the page
      // doesn't throw a console error in front of investors.
    }
  }, []);

  return (
    <div
      className="group relative mx-auto w-full max-w-2xl overflow-hidden rounded-[12px] text-left"
      style={{
        backgroundColor: COLORS.ink2,
        border: `1px solid ${COLORS.borderDark}`,
      }}
    >
      <div className="flex items-center gap-3 px-5 py-4">
        <span
          aria-hidden
          className="select-none text-[13px]"
          style={{ color: COLORS.accentOnInk, fontFamily: MONO }}
        >
          $
        </span>
        <code
          className="flex-1 overflow-x-auto whitespace-nowrap text-[13px]"
          style={{
            color: COLORS.textPrimary,
            fontFamily: MONO,
          }}
        >
          {INSTALL_CMD}
        </code>
        <button
          type="button"
          onClick={onCopy}
          aria-label={copied ? "Copied" : "Copy install command"}
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md transition-colors duration-200"
          style={{
            backgroundColor: copied
              ? "rgba(94,179,168,0.12)"
              : "rgba(255,255,255,0.04)",
            border: `1px solid ${COLORS.borderDark}`,
          }}
        >
          <AnimatePresence mode="wait" initial={false}>
            {copied ? (
              <motion.span
                key="check"
                initial={
                  prefersReduced ? undefined : { opacity: 0, scale: 0.8 }
                }
                animate={{ opacity: 1, scale: 1 }}
                exit={prefersReduced ? undefined : { opacity: 0, scale: 0.8 }}
                transition={{ duration: 0.18, ease: EASE }}
              >
                <CheckIcon color={COLORS.accentOnInk} />
              </motion.span>
            ) : (
              <motion.span
                key="copy"
                initial={
                  prefersReduced ? undefined : { opacity: 0, scale: 0.8 }
                }
                animate={{ opacity: 1, scale: 1 }}
                exit={prefersReduced ? undefined : { opacity: 0, scale: 0.8 }}
                transition={{ duration: 0.18, ease: EASE }}
              >
                <CopyIcon color={COLORS.textSecondary} />
              </motion.span>
            )}
          </AnimatePresence>
        </button>
      </div>
    </div>
  );
}

type SecondaryLink = {
  label: string;
  href: string;
  external?: boolean;
};

const SECONDARY_LINKS: SecondaryLink[] = [
  { label: "Read the docs", href: "/docs/quickstart" },
  {
    label: "Star on GitHub",
    href: "https://github.com/Quorus-dev/Quorus",
    external: true,
  },
  { label: "Join Discord", href: "#" },
];

export default function CTADark() {
  return (
    <section
      data-theme="dark"
      aria-labelledby="cta-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: COLORS.ink }}
    >
      {/* Subtle radial + grain — same family as Control Center, lower intensity
          so the CTA reads as a quieter close, not a second hero. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background: `radial-gradient(ellipse 70% 50% at 50% 50%, rgba(94,179,168,0.07), transparent 70%)`,
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage: `url("${NOISE_SVG}")`,
          backgroundSize: "200px 200px",
          mixBlendMode: "overlay",
        }}
      />

      <div className="relative mx-auto max-w-3xl px-6 py-24 text-center md:py-32">
        <p
          className="text-[11px] uppercase"
          style={{
            color: COLORS.accentOnInk,
            fontFamily: MONO,
            letterSpacing: "0.22em",
          }}
        >
          Get started
        </p>

        <h2
          id="cta-heading"
          className="mt-4 text-balance"
          style={{
            color: COLORS.textPrimary,
            fontFamily: SANS,
            fontSize: "clamp(36px, 4.5vw, 56px)",
            fontWeight: 600,
            lineHeight: 1.05,
            letterSpacing: "-0.02em",
          }}
        >
          Build the future of agentic workflows.
        </h2>

        <div className="mt-10">
          <CodeBlock />
        </div>

        <ul className="mt-8 flex flex-wrap items-center justify-center gap-x-7 gap-y-3">
          {SECONDARY_LINKS.map((link) => (
            <li key={link.label}>
              <a
                href={link.href}
                target={link.external ? "_blank" : undefined}
                rel={link.external ? "noopener noreferrer" : undefined}
                className="inline-flex items-center gap-1.5 text-[13px] transition-colors duration-200"
                style={{
                  color: COLORS.textSecondary,
                  fontFamily: MONO,
                }}
                onMouseEnter={(e) =>
                  (e.currentTarget.style.color = COLORS.textPrimary)
                }
                onMouseLeave={(e) =>
                  (e.currentTarget.style.color = COLORS.textSecondary)
                }
              >
                {link.label}
                <ArrowIcon color="currentColor" />
              </a>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
