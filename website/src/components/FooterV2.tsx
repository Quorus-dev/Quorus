import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

const COLORS = {
  cream: "#f5f1ea",
  cream2: "#ebe5d6",
  ink: "#0a0a0f",
  inkBorder: "rgba(10,10,15,0.08)",
  textPrimary: "#0a0a0f",
  textSecondary: "#4a4a52",
  textMuted: "#7a7a82",
  accent: "#0d4d4a",
} as const;

const SANS = "'Plus Jakarta Sans', system-ui, sans-serif";
const MONO = "'JetBrains Mono', ui-monospace, monospace";

type FooterLink = {
  label: string;
  href: string;
  external?: boolean;
};

const SITEMAP: { title: string; links: FooterLink[] }[] = [
  {
    title: "Product",
    links: [
      { label: "Features", href: "/#features" },
      { label: "Console", href: "/console" },
      {
        label: "Changelog",
        href: "https://github.com/Quorus-dev/Quorus/releases",
        external: true,
      },
    ],
  },
  {
    title: "Resources",
    links: [
      { label: "Docs", href: "/docs" },
      { label: "Quickstart", href: "/docs/quickstart" },
      { label: "MCP tools", href: "/docs/mcp-tools" },
      {
        label: "GitHub",
        href: "https://github.com/Quorus-dev/Quorus",
        external: true,
      },
    ],
  },
  {
    title: "Company",
    // 2026-05-16 audit (B10): About + Blog removed — they pointed to anchors
    // that don't exist. Don't promise what isn't shipped.
    links: [{ label: "Contact", href: "mailto:hello@quorus.dev" }],
  },
];

// 2026-05-16 audit (B10): Status / Security / Privacy were all href="#"
// (dead). Removed until we have real pages to point to.
const BOTTOM_LINKS: FooterLink[] = [];

function FooterAnchor({ link }: { link: FooterLink }) {
  const className = "text-[13px] transition-colors duration-200";
  const onMouseEnter = (e: React.MouseEvent<HTMLElement>) => {
    e.currentTarget.style.color = COLORS.textPrimary;
  };
  const onMouseLeave = (e: React.MouseEvent<HTMLElement>) => {
    e.currentTarget.style.color = COLORS.textSecondary;
  };

  if (link.external || link.href.startsWith("mailto:") || link.href === "#") {
    return (
      <a
        href={link.href}
        target={link.external ? "_blank" : undefined}
        rel={link.external ? "noopener noreferrer" : undefined}
        className={className}
        style={{ color: COLORS.textSecondary, fontFamily: SANS }}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
      >
        {link.label}
      </a>
    );
  }

  // Hash links use anchor tags so smooth-scroll picks them up; route links go
  // through the Router for SPA navigation.
  if (link.href.startsWith("/#")) {
    return (
      <a
        href={link.href}
        className={className}
        style={{ color: COLORS.textSecondary, fontFamily: SANS }}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
      >
        {link.label}
      </a>
    );
  }

  return (
    <Link
      to={link.href}
      className={className}
      style={{ color: COLORS.textSecondary, fontFamily: SANS }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {link.label}
    </Link>
  );
}

function NewsletterForm() {
  const [email, setEmail] = useState("");
  // Style-only — wiring is owned by the main thread later.
  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
  };

  return (
    <form
      onSubmit={onSubmit}
      className="flex w-full max-w-sm overflow-hidden rounded-[12px]"
      style={{
        border: `1px solid ${COLORS.inkBorder}`,
        backgroundColor: COLORS.cream,
      }}
    >
      <label htmlFor="footer-email" className="sr-only">
        Email address
      </label>
      <input
        id="footer-email"
        type="email"
        required
        autoComplete="email"
        placeholder="you@company.com"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        className="min-w-0 flex-1 bg-transparent px-4 py-2.5 text-[13px] outline-none placeholder:opacity-50"
        style={{
          color: COLORS.textPrimary,
          fontFamily: SANS,
        }}
      />
      <button
        type="submit"
        className="shrink-0 px-4 py-2.5 text-[13px] font-medium transition-colors duration-200"
        style={{
          backgroundColor: COLORS.ink,
          color: COLORS.cream,
          fontFamily: SANS,
        }}
      >
        Subscribe
      </button>
    </form>
  );
}

export default function FooterV2() {
  return (
    <footer
      className="w-full"
      style={{
        backgroundColor: COLORS.cream,
        borderTop: `1px solid ${COLORS.inkBorder}`,
      }}
    >
      <div className="mx-auto max-w-7xl px-6 py-16 md:py-20">
        <div className="grid grid-cols-1 gap-12 md:grid-cols-3">
          {/* Brand column */}
          <div>
            <Link
              to="/"
              className="font-semibold tracking-tight"
              style={{
                color: COLORS.textPrimary,
                fontFamily: SANS,
                fontSize: 20,
                fontWeight: 600,
                letterSpacing: "-0.01em",
              }}
            >
              quorus
            </Link>
            <p
              className="mt-4 max-w-xs text-[14px] leading-relaxed"
              style={{ color: COLORS.textSecondary, fontFamily: SANS }}
            >
              Coordination layer for AI agent swarms.
            </p>
            <span
              className="mt-5 inline-flex items-center gap-2 rounded-full px-3 py-1 text-[11px] uppercase"
              style={{
                color: COLORS.accent,
                fontFamily: MONO,
                letterSpacing: "0.16em",
                border: `1px solid ${COLORS.inkBorder}`,
                backgroundColor: COLORS.cream2,
              }}
            >
              <span
                aria-hidden
                className="block h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: COLORS.accent }}
              />
              Open source · MIT
            </span>
          </div>

          {/* Sitemap columns — collapsed under one heading on mobile */}
          <div className="grid grid-cols-2 gap-8 md:col-span-1 md:grid-cols-3">
            {SITEMAP.map((col) => (
              <nav key={col.title} aria-label={col.title}>
                <h3
                  className="mb-4 text-[11px] uppercase"
                  style={{
                    color: COLORS.textMuted,
                    fontFamily: MONO,
                    letterSpacing: "0.18em",
                  }}
                >
                  {col.title}
                </h3>
                <ul className="space-y-2.5">
                  {col.links.map((link) => (
                    <li key={link.label}>
                      <FooterAnchor link={link} />
                    </li>
                  ))}
                </ul>
              </nav>
            ))}
          </div>

          {/* Newsletter */}
          <div>
            <h3
              className="mb-4 text-[11px] uppercase"
              style={{
                color: COLORS.textMuted,
                fontFamily: MONO,
                letterSpacing: "0.18em",
              }}
            >
              Stay in the loop
            </h3>
            <p
              className="mb-4 text-[13px] leading-relaxed"
              style={{ color: COLORS.textSecondary, fontFamily: SANS }}
            >
              Release notes, design notes, and roadmap updates. Roughly monthly.
            </p>
            <NewsletterForm />
          </div>
        </div>

        {/* Hairline divider + bottom row */}
        <div
          className="mt-14 flex flex-col items-start justify-between gap-3 pt-6 md:flex-row md:items-center"
          style={{ borderTop: `1px solid ${COLORS.inkBorder}` }}
        >
          <p
            className="text-[12px]"
            style={{ color: COLORS.textMuted, fontFamily: SANS }}
          >
            © 2026 Quorus
          </p>
          <ul className="flex items-center gap-5">
            {BOTTOM_LINKS.map((link) => (
              <li key={link.label}>
                <FooterAnchor link={link} />
              </li>
            ))}
          </ul>
        </div>
      </div>
    </footer>
  );
}
