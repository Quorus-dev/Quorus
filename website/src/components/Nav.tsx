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

  // Always use dark pill style when scrolled for consistency
  return (
    <div
      className={`fixed z-50 transition-all duration-500 ease-[cubic-bezier(0.4,0,0.2,1)] ${
        scrolled
          ? "top-4 left-1/2 -translate-x-1/2"
          : "top-4 left-1/2 -translate-x-1/2"
      }`}
    >
      <div className="flex items-center transition-all duration-500 ease-[cubic-bezier(0.4,0,0.2,1)] rounded-full border border-white/[0.11] bg-[#06060a]/95 backdrop-blur-2xl shadow-[0_8px_32px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.07)] px-4 py-2 gap-4">
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
        <div className="hidden md:flex items-center gap-6">
          {[
            { label: "Features", id: "features" },
            { label: "Architecture", id: "architecture" },
          ].map((link) => (
            <button
              key={link.label}
              onClick={() => smoothScroll(link.id)}
              className="text-xs text-white/50 hover:text-white/90 transition-colors"
            >
              {link.label}
            </button>
          ))}
        </div>

        {/* Right actions */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => smoothScroll("waitlist")}
            className="rounded-full font-medium bg-teal-600 hover:bg-teal-500 text-white transition-all duration-200 hover:shadow-[0_0_20px_rgba(20,184,166,0.4)] text-xs px-3.5 py-1.5"
          >
            Join beta
          </button>
        </div>
      </div>
    </div>
  );
}
