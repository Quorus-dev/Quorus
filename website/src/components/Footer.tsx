interface FooterLink {
  label: string;
  href: string;
  external?: boolean;
  /** Tagged "soon" — renders dimmed, prevents click. */
  soon?: boolean;
}

const COLUMNS: { title: string; links: FooterLink[] }[] = [
  {
    title: "Product",
    links: [
      { label: "Features", href: "/#features" },
      { label: "Architecture", href: "/#architecture" },
      { label: "Pricing", href: "/pricing" },
      { label: "Console", href: "/console" },
    ],
  },
  {
    title: "Resources",
    links: [
      { label: "Docs", href: "/docs/quickstart" },
      { label: "MCP tools", href: "/docs/mcp-tools" },
      { label: "Why cross-vendor", href: "/docs/why-cross-vendor" },
      {
        label: "Changelog",
        href: "https://github.com/Quorus-dev/Quorus/releases",
        external: true,
      },
    ],
  },
  {
    title: "Community",
    links: [
      {
        label: "GitHub",
        href: "https://github.com/Quorus-dev/Quorus",
        external: true,
      },
      { label: "Discord", href: "#", soon: true },
      { label: "X / Twitter", href: "https://x.com/quorusdev", external: true },
      { label: "Status", href: "#", soon: true },
    ],
  },
  {
    title: "Legal",
    links: [
      { label: "Privacy", href: "/privacy", soon: true },
      { label: "Terms", href: "/terms", soon: true },
      {
        label: "License (MIT)",
        href: "https://github.com/Quorus-dev/Quorus/blob/main/LICENSE",
        external: true,
      },
    ],
  },
];

function FooterAnchor({ link }: { link: FooterLink }) {
  const className =
    "text-sm transition-colors focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 rounded";

  if (link.soon) {
    return (
      <span
        className={`${className} text-white/30 cursor-not-allowed inline-flex items-center gap-1.5`}
        aria-disabled="true"
        title="Coming soon"
      >
        {link.label}
        <span className="text-[9px] font-mono text-white/30 border border-white/10 rounded px-1 py-px tracking-widest uppercase">
          Soon
        </span>
      </span>
    );
  }
  if (link.external) {
    return (
      <a
        href={link.href}
        target="_blank"
        rel="noopener noreferrer"
        className={`${className} text-white/55 hover:text-white`}
      >
        {link.label}
      </a>
    );
  }
  return (
    <a
      href={link.href}
      className={`${className} text-white/55 hover:text-white`}
    >
      {link.label}
    </a>
  );
}

export default function Footer() {
  return (
    <footer className="border-t border-white/5 pt-16 pb-10 px-6 mt-auto">
      <div className="max-w-7xl mx-auto">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-8 mb-12">
          {/* Brand */}
          <div className="col-span-2 md:col-span-1">
            <a href="/" className="flex items-center gap-2.5 mb-3">
              <div className="relative w-2 h-2">
                <div className="w-2 h-2 rounded-full bg-teal-500 pulse-dot" />
                <div className="absolute inset-0 rounded-full bg-teal-500 opacity-30 scale-[2.5] pulse-dot" />
              </div>
              <span className="font-mono text-base font-semibold text-white">
                quorus
              </span>
            </a>
            <p className="text-xs text-white/45 leading-relaxed max-w-[18ch]">
              Coordination layer for AI coding agents. Open-source, MIT.
            </p>
          </div>

          {COLUMNS.map((col) => (
            <nav key={col.title} aria-label={col.title}>
              <h3 className="text-[11px] font-mono text-white/35 tracking-[0.18em] uppercase mb-3">
                {col.title}
              </h3>
              <ul className="space-y-2">
                {col.links.map((link) => (
                  <li key={link.label}>
                    <FooterAnchor link={link} />
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>

        <div className="pt-6 border-t border-white/5 flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
          <p className="text-xs text-white/40 font-mono">
            © 2026 Quorus · Built by Arav &amp; Aarya
          </p>
          <p className="text-xs text-white/35 font-mono">
            v0.4.0 · MIT licensed · Made for builders
          </p>
        </div>
      </div>
    </footer>
  );
}
