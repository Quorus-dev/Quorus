import { useState, useEffect, useRef, useCallback } from "react";
import { Link, useLocation } from "react-router-dom";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";

// Design contract — hardcoded until tokens.css ships from frontend-design.
const COLORS = {
  cream: "#f5f1ea",
  ink: "#0a0a0f",
  inkSecondary: "#a8a8b0",
  textOnCreamPrimary: "#0a0a0f",
  textOnCreamSecondary: "#4a4a52",
} as const;

type NavLink = { label: string; href: string; external?: boolean };

const NAV_LINKS: NavLink[] = [
  { label: "Features", href: "/#features" },
  { label: "Console", href: "/console" },
  { label: "Docs", href: "/docs/quickstart" },
];

const GITHUB_URL = "https://github.com/Quorus-dev/Quorus";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * Detect whether the viewport is currently over a section that opted into the
 * dark theme (`data-theme="dark"`). We watch all such sections with a single
 * IntersectionObserver tuned so the nav inverts the moment the dark band
 * crosses the top of the viewport.
 */
function useDarkSectionTheme(): boolean {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const elements = Array.from(
      document.querySelectorAll<HTMLElement>('[data-theme="dark"]'),
    );
    if (elements.length === 0) return;

    // Track which dark sections are currently intersecting the top band.
    const visible = new Set<Element>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) visible.add(entry.target);
          else visible.delete(entry.target);
        }
        setIsDark(visible.size > 0);
      },
      {
        // Top 80px of the viewport is where the nav lives. Trigger when a dark
        // section crosses that band.
        rootMargin: "0px 0px -90% 0px",
        threshold: 0,
      },
    );

    elements.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  return isDark;
}

function useScrolled(threshold = 80): boolean {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > threshold);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [threshold]);
  return scrolled;
}

function HamburgerIcon({ color }: { color: string }) {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden>
      <path
        d="M3 6h14M3 10h14M3 14h14"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function CloseIcon({ color }: { color: string }) {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden>
      <path
        d="M5 5l10 10M15 5L5 15"
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function GitHubIcon({ color }: { color: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill={color} aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

export default function NavV2() {
  const location = useLocation();
  const scrolled = useScrolled(80);
  const isDark = useDarkSectionTheme();
  const prefersReduced = useReducedMotion();
  const [mobileOpen, setMobileOpen] = useState(false);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);

  // Close the sheet on route change.
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname, location.hash]);

  // Lock body scroll while the mobile sheet is open + return focus.
  useEffect(() => {
    if (!mobileOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeBtnRef.current?.focus();
    return () => {
      document.body.style.overflow = prev;
    };
  }, [mobileOpen]);

  // Esc closes the sheet.
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileOpen]);

  const onLinkClick = useCallback(() => setMobileOpen(false), []);

  // Color choreography — derived from theme state.
  const fg = isDark ? COLORS.cream : COLORS.textOnCreamPrimary;
  const fgSecondary = isDark
    ? COLORS.inkSecondary
    : COLORS.textOnCreamSecondary;
  const bg = scrolled
    ? isDark
      ? "rgba(10,10,15,0.85)"
      : "rgba(245,241,234,0.85)"
    : "transparent";
  const borderBottom =
    scrolled && !isDark ? "rgba(10,10,15,0.08)" : "transparent";
  const ctaBg = isDark ? COLORS.cream : COLORS.ink;
  const ctaFg = isDark ? COLORS.ink : COLORS.cream;

  // Plain anchors for hash links so smooth-scroll behaves; Link for SPA routes.
  const renderLink = (link: NavLink) => {
    const isHashOrExternal = link.href.startsWith("/#") || link.external;
    const className =
      "text-[13px] font-medium transition-colors duration-200 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 rounded-sm";
    const style: React.CSSProperties = {
      color: fgSecondary,
      transition: "color 0.2s ease",
    };
    const onMouseEnter = (e: React.MouseEvent<HTMLElement>) => {
      e.currentTarget.style.color = fg;
    };
    const onMouseLeave = (e: React.MouseEvent<HTMLElement>) => {
      e.currentTarget.style.color = fgSecondary;
    };

    if (isHashOrExternal) {
      return (
        <a
          key={link.label}
          href={link.href}
          target={link.external ? "_blank" : undefined}
          rel={link.external ? "noopener noreferrer" : undefined}
          className={className}
          style={style}
          onMouseEnter={onMouseEnter}
          onMouseLeave={onMouseLeave}
          onClick={onLinkClick}
        >
          {link.label}
        </a>
      );
    }
    return (
      <Link
        key={link.label}
        to={link.href}
        className={className}
        style={style}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
        onClick={onLinkClick}
      >
        {link.label}
      </Link>
    );
  };

  return (
    <>
      <motion.header
        // The nav itself is a flat top bar (NOT a floating pill).
        className="fixed top-0 inset-x-0 z-50 w-full"
        initial={false}
        animate={{
          backgroundColor: bg,
          borderBottomColor: borderBottom,
        }}
        transition={{
          duration: prefersReduced ? 0 : 0.35,
          ease: EASE,
        }}
        style={{
          backdropFilter: scrolled ? "blur(12px)" : "none",
          WebkitBackdropFilter: scrolled ? "blur(12px)" : "none",
          borderBottomWidth: 1,
          borderBottomStyle: "solid",
        }}
      >
        <nav
          className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6"
          aria-label="Primary"
        >
          {/* Wordmark */}
          <Link
            to="/"
            className="font-semibold tracking-tight transition-colors duration-200"
            style={{
              color: fg,
              fontFamily: "'Plus Jakarta Sans', system-ui, sans-serif",
              fontSize: 18,
              fontWeight: 600,
              letterSpacing: "-0.01em",
            }}
            aria-label="Quorus home"
          >
            quorus
          </Link>

          {/* Center links — desktop only */}
          <div className="hidden items-center gap-7 md:flex">
            {NAV_LINKS.map(renderLink)}
          </div>

          {/* Right cluster */}
          <div className="hidden items-center gap-4 md:flex">
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-[13px] font-medium transition-colors duration-200"
              style={{ color: fgSecondary }}
              onMouseEnter={(e) => (e.currentTarget.style.color = fg)}
              onMouseLeave={(e) => (e.currentTarget.style.color = fgSecondary)}
              aria-label="View Quorus on GitHub"
            >
              <GitHubIcon color="currentColor" />
              GitHub
            </a>
            <motion.a
              href="/#waitlist"
              className="inline-flex items-center justify-center rounded-[12px] px-4 py-2 text-[13px] font-medium"
              style={{
                backgroundColor: ctaBg,
                color: ctaFg,
                transition: "background-color 0.25s ease, color 0.25s ease",
              }}
              whileHover={prefersReduced ? undefined : { scale: 1.02 }}
              whileTap={prefersReduced ? undefined : { scale: 0.98 }}
              transition={{ duration: 0.2, ease: EASE }}
              onClick={onLinkClick}
            >
              Join waitlist
            </motion.a>
          </div>

          {/* Mobile trigger */}
          <button
            type="button"
            className="inline-flex h-10 w-10 items-center justify-center rounded-md md:hidden"
            aria-label="Open menu"
            aria-expanded={mobileOpen}
            aria-controls="mobile-nav-sheet"
            onClick={() => setMobileOpen(true)}
          >
            <HamburgerIcon color={fg} />
          </button>
        </nav>
      </motion.header>

      {/* Mobile full-screen sheet (NOT a dropdown). */}
      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            id="mobile-nav-sheet"
            role="dialog"
            aria-modal="true"
            aria-label="Mobile navigation"
            className="fixed inset-0 z-[60] flex flex-col md:hidden"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: prefersReduced ? 0 : 0.25, ease: EASE }}
            style={{ backgroundColor: COLORS.cream }}
          >
            <div className="flex h-16 items-center justify-between px-6">
              <span
                className="font-semibold"
                style={{
                  color: COLORS.textOnCreamPrimary,
                  fontFamily: "'Plus Jakarta Sans', system-ui, sans-serif",
                  fontSize: 18,
                  fontWeight: 600,
                  letterSpacing: "-0.01em",
                }}
              >
                quorus
              </span>
              <button
                ref={closeBtnRef}
                type="button"
                className="inline-flex h-10 w-10 items-center justify-center rounded-md"
                aria-label="Close menu"
                onClick={() => setMobileOpen(false)}
              >
                <CloseIcon color={COLORS.textOnCreamPrimary} />
              </button>
            </div>

            <motion.div
              className="flex flex-1 flex-col gap-1 px-6 pt-8"
              initial="hidden"
              animate="show"
              variants={{
                hidden: {},
                show: { transition: { staggerChildren: 0.05 } },
              }}
            >
              {NAV_LINKS.map((link) => (
                <motion.div
                  key={link.label}
                  variants={{
                    hidden: { opacity: 0, y: 12 },
                    show: { opacity: 1, y: 0 },
                  }}
                  transition={{ duration: 0.4, ease: EASE }}
                >
                  {link.href.startsWith("/#") ? (
                    <a
                      href={link.href}
                      onClick={onLinkClick}
                      className="block py-3 text-2xl font-semibold tracking-tight"
                      style={{
                        color: COLORS.textOnCreamPrimary,
                        fontFamily:
                          "'Plus Jakarta Sans', system-ui, sans-serif",
                        letterSpacing: "-0.02em",
                      }}
                    >
                      {link.label}
                    </a>
                  ) : (
                    <Link
                      to={link.href}
                      onClick={onLinkClick}
                      className="block py-3 text-2xl font-semibold tracking-tight"
                      style={{
                        color: COLORS.textOnCreamPrimary,
                        fontFamily:
                          "'Plus Jakarta Sans', system-ui, sans-serif",
                        letterSpacing: "-0.02em",
                      }}
                    >
                      {link.label}
                    </Link>
                  )}
                </motion.div>
              ))}
              <motion.a
                href={GITHUB_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-flex items-center gap-2 py-3 text-base"
                style={{ color: COLORS.textOnCreamSecondary }}
                variants={{
                  hidden: { opacity: 0, y: 12 },
                  show: { opacity: 1, y: 0 },
                }}
                transition={{ duration: 0.4, ease: EASE }}
              >
                <GitHubIcon color={COLORS.textOnCreamSecondary} />
                GitHub
              </motion.a>
            </motion.div>

            <div className="px-6 pb-10">
              <a
                href="/#waitlist"
                onClick={onLinkClick}
                className="block w-full rounded-[12px] py-3 text-center text-base font-medium"
                style={{
                  backgroundColor: COLORS.ink,
                  color: COLORS.cream,
                }}
              >
                Join waitlist
              </a>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
