"use client";

import { useState, useEffect } from "react";

function smoothScroll(id: string) {
  const el = document.getElementById(id);
  el?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export default function Nav() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <nav
      className={`fixed top-10 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled
          ? "bg-[#06060a]/85 backdrop-blur-xl border-b border-white/5 shadow-[0_1px_0_rgba(124,58,237,0.08)]"
          : "bg-transparent"
      }`}
    >
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        {/* Logo */}
        <a href="/" className="flex items-center gap-2.5 group">
          <div className="relative">
            <div className="w-2 h-2 rounded-full bg-violet-500 pulse-dot" />
            <div className="absolute inset-0 w-2 h-2 rounded-full bg-violet-500 opacity-40 scale-150 pulse-dot" />
          </div>
          <span className="font-mono text-lg font-semibold tracking-tight text-white group-hover:text-violet-300 transition-colors">
            murmur
          </span>
        </a>

        {/* Links */}
        <div className="hidden md:flex items-center gap-8">
          {[
            { label: "Features", id: "features" },
            { label: "How it works", id: "howit" },
            { label: "Architecture", id: "architecture" },
          ].map((link) => (
            <button
              key={link.label}
              onClick={() => smoothScroll(link.id)}
              className="text-sm text-white/40 hover:text-white/80 transition-colors"
            >
              {link.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3">
          {/* Console link */}
          <a
            href="/console"
            className="hidden md:flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono text-white/40 hover:text-white/70 border border-white/8 hover:border-white/15 bg-white/[0.02] hover:bg-white/[0.05] transition-all duration-200"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-green-400/70" />
            console
          </a>

          {/* CTA */}
          <button
            onClick={() => smoothScroll("waitlist")}
            className="px-4 py-2 rounded-full text-sm font-medium bg-violet-600 hover:bg-violet-500 text-white transition-all duration-200 hover:shadow-lg hover:shadow-violet-500/25"
          >
            Request access
          </button>
        </div>
      </div>
    </nav>
  );
}
