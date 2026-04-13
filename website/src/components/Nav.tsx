import { useState, useEffect } from "react";

function smoothScroll(id: string) {
  const el = document.getElementById(id);
  el?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export default function Nav() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 80);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div
      className={`fixed z-50 transition-all duration-500 ease-[cubic-bezier(0.4,0,0.2,1)] ${
        scrolled ? "top-4 left-1/2 -translate-x-1/2" : "top-10 left-0 right-0"
      }`}
    >
      <div
        className={`flex items-center transition-all duration-500 ease-[cubic-bezier(0.4,0,0.2,1)] ${
          scrolled
            ? "rounded-full border border-white/[0.11] bg-[#06060a]/92 backdrop-blur-2xl shadow-[0_8px_32px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.07)] px-4 py-2 gap-4"
            : "max-w-7xl mx-auto px-6 h-16 gap-8 justify-between"
        }`}
      >
        {/* Logo */}
        <a href="/" className="flex items-center gap-2 group shrink-0">
          <div className="relative w-2 h-2">
            <div className="w-2 h-2 rounded-full bg-teal-500 pulse-dot" />
            <div className="absolute inset-0 rounded-full bg-teal-500 opacity-30 scale-[2.5] pulse-dot" />
          </div>
          <span className="font-mono text-sm font-semibold tracking-tight text-white group-hover:text-teal-300 transition-colors">
            murmur
          </span>
        </a>

        {/* Center nav links */}
        <div
          className={`items-center gap-6 ${scrolled ? "flex" : "hidden md:flex"}`}
        >
          {[
            { label: "Features", id: "features" },
            { label: "How it works", id: "howit" },
            { label: "Architecture", id: "architecture" },
          ].map((link) => (
            <button
              key={link.label}
              onClick={() => smoothScroll(link.id)}
              className="text-xs text-white/40 hover:text-white/80 transition-colors"
            >
              {link.label}
            </button>
          ))}
        </div>

        {/* Right actions */}
        <div className="flex items-center gap-2 shrink-0">
          {!scrolled && (
            <a
              href="/console"
              className="hidden md:flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono text-white/40 hover:text-white/70 border border-white/[0.08] hover:border-white/[0.15] bg-white/[0.02] hover:bg-white/[0.05] transition-all duration-200"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-green-400/70" />
              console
            </a>
          )}
          <button
            onClick={() => smoothScroll("waitlist")}
            className={`rounded-full font-medium bg-teal-600 hover:bg-teal-500 text-white transition-all duration-200 hover:shadow-[0_0_20px_rgba(20,184,166,0.4)] ${
              scrolled ? "text-xs px-3.5 py-1.5" : "text-sm px-4 py-2"
            }`}
          >
            {scrolled ? "Join beta" : "Request access"}
          </button>
        </div>
      </div>
    </div>
  );
}
