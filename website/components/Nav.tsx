"use client";

import { useState, useEffect } from "react";

export default function Nav() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <nav
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled
          ? "bg-black/80 backdrop-blur-xl border-b border-white/5"
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
            { label: "Features", href: "#features" },
            { label: "How it works", href: "#howit" },
            { label: "Architecture", href: "#architecture" },
          ].map((link) => (
            <a
              key={link.label}
              href={link.href}
              className="text-sm text-white/40 hover:text-white/80 transition-colors"
            >
              {link.label}
            </a>
          ))}
        </div>

        {/* CTA */}
        <a
          href="#waitlist"
          className="px-4 py-2 rounded-full text-sm font-medium bg-violet-600 hover:bg-violet-500 text-white transition-all duration-200 hover:shadow-lg hover:shadow-violet-500/25"
        >
          Request access
        </a>
      </div>
    </nav>
  );
}
