import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useEffect } from "react";
import Footer from "./Footer";

const SECTIONS: { title: string; items: { to: string; label: string }[] }[] = [
  {
    title: "Get started",
    items: [{ to: "/docs/quickstart", label: "Quickstart" }],
  },
  {
    title: "Reference",
    items: [{ to: "/docs/mcp-tools", label: "MCP tools" }],
  },
  {
    title: "Concepts",
    items: [{ to: "/docs/why-cross-vendor", label: "Why cross-vendor" }],
  },
];

export default function DocsLayout() {
  const { pathname } = useLocation();

  // Reset scroll on route change so docs feel like a real docs site, not a SPA
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  return (
    <div
      className="min-h-screen flex flex-col"
      style={{ background: "var(--background)" }}
    >
      <a
        href="#docs-main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[100] focus:px-3 focus:py-2 focus:rounded-md focus:bg-teal-500 focus:text-black focus:font-mono focus:text-xs"
      >
        Skip to docs content
      </a>

      {/* Slim docs nav */}
      <header className="sticky top-0 z-40 backdrop-blur-xl bg-[#06060a]/80 border-b border-white/[0.06]">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2 group shrink-0">
            <span className="relative w-2 h-2">
              <span className="block w-2 h-2 rounded-full bg-teal-500 pulse-dot" />
            </span>
            <span className="font-mono text-sm font-semibold tracking-tight text-white group-hover:text-teal-300 transition-colors">
              quorus
            </span>
            <span className="text-white/30 text-sm">/ docs</span>
          </Link>
          <nav aria-label="Top">
            <ul className="flex items-center gap-5 text-xs text-white/60">
              <li>
                <Link to="/" className="hover:text-white/95 transition-colors">
                  Home
                </Link>
              </li>
              <li>
                <a
                  href="https://github.com/Quorus-dev/Quorus"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-white/95 transition-colors"
                >
                  GitHub
                </a>
              </li>
            </ul>
          </nav>
        </div>
      </header>

      <div className="flex-1">
        <div className="max-w-6xl mx-auto px-6 py-10 grid grid-cols-1 md:grid-cols-[220px_1fr] gap-10">
          <aside
            aria-label="Docs navigation"
            className="md:sticky md:top-20 md:self-start"
          >
            <nav>
              {SECTIONS.map((section) => (
                <div key={section.title} className="mb-6">
                  <p className="text-[11px] font-mono tracking-widest uppercase text-white/40 mb-3">
                    {section.title}
                  </p>
                  <ul className="space-y-1">
                    {section.items.map((item) => (
                      <li key={item.to}>
                        <NavLink
                          to={item.to}
                          end
                          className={({ isActive }) =>
                            `block px-3 py-1.5 rounded-md text-sm transition-colors ${
                              isActive
                                ? "text-teal-300 bg-teal-500/[0.08] border border-teal-500/20"
                                : "text-white/65 border border-transparent hover:text-white hover:bg-white/[0.03]"
                            }`
                          }
                        >
                          {item.label}
                        </NavLink>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </nav>
          </aside>

          <main
            id="docs-main"
            className="min-w-0 prose-invert max-w-none text-white/85"
          >
            <Outlet />
          </main>
        </div>
      </div>

      <Footer />
    </div>
  );
}
