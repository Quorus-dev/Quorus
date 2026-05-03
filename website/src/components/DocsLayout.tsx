import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import FooterV2 from "./FooterV2";

/**
 * DocsLayout — cream/ink docs chrome.
 *
 * Desktop (>=lg): two-column. Sticky 260px sidebar with hairline right border,
 * primary content column with a comfortable prose width (~720px).
 *
 * Mobile (<lg): main content first; sidebar collapses into an "On this page"
 * disclosure drawer pinned just under the global NavV2.
 *
 * NavV2 is mounted globally in App.tsx, so this layout never renders its own
 * top bar — it just pads enough to clear the fixed nav (~80px).
 */

const EASE = [0.16, 1, 0.3, 1] as const;

type DocItem = { to: string; label: string; soon?: boolean };
type DocSection = { title: string; items: DocItem[] };

const SECTIONS: DocSection[] = [
  {
    title: "Getting started",
    items: [
      { to: "/docs", label: "Overview" },
      { to: "/docs/quickstart", label: "Quickstart" },
      { to: "/docs/mcp-tools", label: "MCP tools" },
      { to: "/docs/why-cross-vendor", label: "Why cross-vendor" },
    ],
  },
  {
    title: "Guides",
    items: [{ to: "/docs/guides", label: "Coming soon", soon: true }],
  },
  {
    title: "Reference",
    items: [{ to: "/docs/reference", label: "Coming soon", soon: true }],
  },
];

export default function DocsLayout() {
  const { pathname } = useLocation();
  const prefersReduced = useReducedMotion();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Reset scroll on route change so docs feel like a real docs site
  useEffect(() => {
    window.scrollTo(0, 0);
    setDrawerOpen(false);
  }, [pathname]);

  return (
    <div
      className="flex min-h-screen flex-col"
      style={{ backgroundColor: "var(--color-cream)" }}
    >
      <a
        href="#docs-main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[100] focus:rounded-md focus:px-3 focus:py-2 focus:font-mono focus:text-xs"
        style={{
          backgroundColor: "var(--color-accent)",
          color: "var(--color-cream)",
        }}
      >
        Skip to docs content
      </a>

      {/* Mobile section toggle — pinned just below the global NavV2.
          Hidden on lg+ where the persistent sidebar takes over. */}
      <div
        className="sticky top-16 z-30 lg:hidden"
        style={{
          backgroundColor: "rgba(245,241,234,0.92)",
          backdropFilter: "blur(8px)",
          WebkitBackdropFilter: "blur(8px)",
          borderBottom: "1px solid var(--color-border-light)",
        }}
      >
        <button
          type="button"
          aria-expanded={drawerOpen}
          aria-controls="docs-mobile-drawer"
          onClick={() => setDrawerOpen((v) => !v)}
          className="mx-auto flex w-full max-w-7xl items-center justify-between px-6 py-3 text-left"
        >
          <span
            className="font-mono text-[11px] uppercase tracking-[0.18em]"
            style={{ color: "var(--color-text-on-cream-muted)" }}
          >
            Documentation
          </span>
          <span
            className="inline-flex items-center gap-2 text-[13px]"
            style={{ color: "var(--color-text-on-cream)" }}
          >
            On this page
            <svg
              width="12"
              height="12"
              viewBox="0 0 12 12"
              fill="none"
              aria-hidden="true"
              style={{
                transform: drawerOpen ? "rotate(180deg)" : "rotate(0deg)",
                transition: "transform 0.25s var(--motion-ease-out)",
              }}
            >
              <path
                d="M3 4.5l3 3 3-3"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
        </button>

        <AnimatePresence initial={false}>
          {drawerOpen && (
            <motion.div
              id="docs-mobile-drawer"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: prefersReduced ? 0 : 0.3, ease: EASE }}
              className="overflow-hidden"
              style={{ borderTop: "1px solid var(--color-border-light)" }}
            >
              <div className="mx-auto max-w-7xl px-6 py-5">
                <SidebarNav onNavigate={() => setDrawerOpen(false)} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="flex-1">
        <div className="mx-auto grid w-full max-w-7xl grid-cols-1 gap-10 px-6 pb-20 pt-8 lg:grid-cols-[260px_minmax(0,1fr)] lg:gap-14 lg:pt-28">
          {/* Sticky sidebar — desktop only */}
          <aside
            aria-label="Docs navigation"
            className="hidden lg:block"
            style={{
              borderRight: "1px solid var(--color-border-light)",
            }}
          >
            <div className="sticky top-28 pr-8">
              <p
                className="mb-6 font-mono text-[11px] uppercase tracking-[0.18em]"
                style={{ color: "var(--color-text-on-cream-muted)" }}
              >
                Documentation
              </p>
              <SidebarNav />
            </div>
          </aside>

          {/* Main content column — capped at a prose-friendly width */}
          <main
            id="docs-main"
            className="min-w-0"
            style={{ maxWidth: "720px" }}
          >
            <Outlet />
          </main>
        </div>
      </div>

      <FooterV2 />
    </div>
  );
}

/**
 * SidebarNav — the link list. Reused by the desktop sidebar AND the mobile
 * disclosure drawer so both surfaces stay in lockstep.
 */
function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav>
      {SECTIONS.map((section, i) => (
        <div key={section.title} className={i === 0 ? "mb-7" : "mb-7 mt-2"}>
          <p
            className="mb-3 font-mono text-[10.5px] uppercase tracking-[0.2em]"
            style={{ color: "var(--color-text-on-cream-muted)" }}
          >
            {section.title}
          </p>
          <ul className="flex flex-col gap-0.5">
            {section.items.map((item) => (
              <li key={item.to}>
                {item.soon ? (
                  <span
                    className="block py-1.5 pl-3 text-[14px]"
                    style={{
                      color: "var(--color-text-on-cream-muted)",
                      borderLeft: "2px solid transparent",
                    }}
                  >
                    {item.label}
                  </span>
                ) : (
                  <SidebarLink
                    to={item.to}
                    label={item.label}
                    onNavigate={onNavigate}
                  />
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}

      <div
        className="mt-8 pt-6"
        style={{ borderTop: "1px solid var(--color-border-light)" }}
      >
        <a
          href="https://github.com/Quorus-dev/Quorus"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 text-[13px] transition-colors"
          style={{ color: "var(--color-text-on-cream-secondary)" }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = "var(--color-text-on-cream)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color =
              "var(--color-text-on-cream-secondary)";
          }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 16 16"
            fill="currentColor"
            aria-hidden="true"
          >
            <path d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
          </svg>
          View on GitHub
        </a>
      </div>
    </nav>
  );
}

function SidebarLink({
  to,
  label,
  onNavigate,
}: {
  to: string;
  label: string;
  onNavigate?: () => void;
}) {
  return (
    <NavLink
      to={to}
      end
      onClick={onNavigate}
      className={({ isActive }) =>
        `block py-1.5 pl-3 text-[14px] transition-colors ${
          isActive ? "font-medium" : ""
        }`
      }
      style={({ isActive }) => ({
        color: isActive
          ? "var(--color-accent)"
          : "var(--color-text-on-cream-secondary)",
        borderLeft: isActive
          ? "2px solid var(--color-accent)"
          : "2px solid transparent",
        marginLeft: "-2px",
      })}
    >
      {label}
    </NavLink>
  );
}

// Re-export Link so callers don't need to pull from react-router-dom themselves
// when this layout is the only context they use it from.
export { Link };
