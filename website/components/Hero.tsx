"use client";

import { useState } from "react";

const INSTALL_CMD = `pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"`;

export default function Hero() {
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(INSTALL_CMD);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="relative min-h-screen flex flex-col items-center justify-center overflow-hidden grid-bg">
      {/* Radial glow */}
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] rounded-full bg-violet-600/10 blur-[120px] pointer-events-none" />
      <div className="absolute top-1/3 left-1/3 w-[400px] h-[400px] rounded-full bg-cyan-500/5 blur-[100px] pointer-events-none" />

      <div className="relative z-10 flex flex-col items-center text-center px-6 max-w-5xl mx-auto pt-24">
        {/* Badge */}
        <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-white/10 bg-white/5 text-xs text-white/60 mb-8 backdrop-blur-sm">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 pulse-dot" />
          Open source · MIT licensed · 780+ tests
        </div>

        {/* Headline */}
        <h1 className="text-5xl md:text-7xl lg:text-8xl font-bold tracking-tight leading-none mb-6">
          <span className="gradient-text">The Communication</span>
          <br />
          <span className="text-white">Layer for AI Swarms</span>
        </h1>

        {/* Subheading */}
        <p className="text-lg md:text-xl text-white/50 max-w-2xl mb-10 leading-relaxed">
          Any model. Any machine. Real-time coordination.
          <br className="hidden md:block" />
          Rooms, SSE push, shared state, distributed locks — for every AI agent.
        </p>

        {/* Install command */}
        <div className="w-full max-w-2xl mb-8">
          <div
            className="code-block flex items-center justify-between px-5 py-3.5 group cursor-pointer"
            onClick={copy}
          >
            <div className="flex items-center gap-3 min-w-0">
              <span className="text-white/30 font-mono text-sm shrink-0">
                $
              </span>
              <span className="font-mono text-sm text-green-400 truncate">
                {INSTALL_CMD}
              </span>
            </div>
            <button className="ml-4 shrink-0 p-1.5 rounded-md text-white/30 hover:text-white/80 hover:bg-white/10 transition-all">
              {copied ? (
                <svg
                  className="w-4 h-4 text-green-400"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M5 13l4 4L19 7"
                  />
                </svg>
              ) : (
                <svg
                  className="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                  />
                </svg>
              )}
            </button>
          </div>
        </div>

        {/* CTAs */}
        <div className="flex flex-col sm:flex-row gap-3 mb-16">
          <a
            href="https://github.com/Aarya2004/murmur"
            target="_blank"
            rel="noopener noreferrer"
            className="px-6 py-3 rounded-full bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-all duration-200 hover:shadow-lg hover:shadow-violet-500/20"
          >
            Read the Docs →
          </a>
          <a
            href="https://github.com/Aarya2004/murmur"
            target="_blank"
            rel="noopener noreferrer"
            className="px-6 py-3 rounded-full border border-white/10 hover:border-white/20 bg-white/5 hover:bg-white/10 text-white text-sm font-medium transition-all duration-200"
          >
            View on GitHub
          </a>
        </div>

        {/* Floating badges */}
        <div className="flex flex-wrap items-center justify-center gap-2 text-xs text-white/40">
          {[
            "SSE push delivery",
            "Zero polling",
            "At-least-once ACK",
            "Redis-backed",
            "MIT licensed",
            "780 tests",
          ].map((badge) => (
            <span
              key={badge}
              className="px-3 py-1 rounded-full border border-white/8 bg-white/3"
            >
              {badge}
            </span>
          ))}
        </div>
      </div>

      {/* Scroll indicator */}
      <div className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2 text-white/20">
        <span className="text-xs font-mono">scroll</span>
        <div className="w-px h-8 bg-gradient-to-b from-white/20 to-transparent" />
      </div>
    </section>
  );
}
