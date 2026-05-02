import { useEffect, useRef, useState } from "react";

interface VideoDemoProps {
  /**
   * Video source. If null/undefined, renders a "Demo coming soon" placeholder
   * card with the same aspect ratio so layout doesn't shift when the video
   * eventually drops in.
   */
  src?: string | null;
  /** Optional poster frame for LCP. */
  poster?: string;
  /** Optional caption shown beneath the player for context. */
  caption?: string;
  /** Date the demo will land. Used by the placeholder when src is absent. */
  comingDate?: string;
  /**
   * Optional Loom share URL for an iframe embed. Used when `src` is the
   * literal string "loom" — keeps the API tiny.
   */
  loomUrl?: string;
}

/**
 * 90-second demo embed. Plays muted + looped + inline so it can autoplay
 * everywhere (incl. iOS). preload="metadata" keeps the LCP honest — only the
 * poster + first metadata block are fetched until the user is on-screen.
 */
export default function VideoDemo({
  src,
  poster,
  caption = "90-second walkthrough · Claude Code, Cursor and Codex coordinating in one room",
  comingDate = "May 4, 2026",
  loomUrl,
}: VideoDemoProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [reduced, setReduced] = useState(false);

  // Respect prefers-reduced-motion: no autoplay, but keep controls.
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);

  // Pause when off-screen (battery, INP).
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const obs = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            if (!reduced) v.play().catch(() => {});
          } else {
            v.pause();
          }
        }
      },
      { threshold: 0.25 },
    );
    obs.observe(v);
    return () => obs.disconnect();
  }, [reduced]);

  // ── Loom iframe variant ────────────────────────────────────────────────
  if (src === "loom" && loomUrl) {
    return (
      <figure className="w-full max-w-4xl mx-auto">
        <div
          className="relative rounded-2xl overflow-hidden border border-white/10 bg-black/60 shadow-2xl shadow-black/50"
          style={{ aspectRatio: "16 / 9" }}
        >
          <iframe
            src={loomUrl}
            title="Quorus 90-second product walkthrough"
            allow="fullscreen; picture-in-picture; clipboard-write"
            allowFullScreen
            loading="lazy"
            className="absolute inset-0 w-full h-full"
          />
        </div>
        {caption ? (
          <figcaption className="text-center text-xs text-white/45 font-mono mt-3">
            {caption}
          </figcaption>
        ) : null}
      </figure>
    );
  }

  // ── Placeholder when no video source yet ───────────────────────────────
  if (!src) {
    return (
      <figure
        className="w-full max-w-4xl mx-auto"
        aria-label="Demo coming soon"
      >
        <div
          className="relative rounded-2xl overflow-hidden border border-white/10 bg-gradient-to-br from-[#0d0d1c] via-[#0a0a14] to-[#08080f] shadow-2xl shadow-black/50 flex items-center justify-center"
          style={{ aspectRatio: "16 / 9" }}
        >
          {/* Subtle grid */}
          <div
            className="absolute inset-0 opacity-40"
            style={{
              backgroundImage:
                "linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px)",
              backgroundSize: "32px 32px",
            }}
            aria-hidden="true"
          />
          {/* Teal radial glow */}
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              background:
                "radial-gradient(ellipse 60% 50% at 50% 50%, rgba(20,184,166,0.15) 0%, transparent 70%)",
            }}
            aria-hidden="true"
          />

          <div className="relative z-10 text-center px-6">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-teal-500/30 bg-teal-500/10 text-[11px] font-mono text-teal-300 mb-5">
              <span className="w-1.5 h-1.5 rounded-full bg-teal-400 pulse-dot" />
              Recording in progress
            </div>
            <h3 className="text-2xl md:text-3xl font-semibold text-white mb-2 tracking-tight">
              90-second walkthrough
            </h3>
            <p className="text-white/55 text-sm md:text-base">
              Demo lands {comingDate}.
            </p>
          </div>
        </div>
        {caption ? (
          <figcaption className="text-center text-xs text-white/45 font-mono mt-3">
            {caption}
          </figcaption>
        ) : null}
      </figure>
    );
  }

  // ── Real video ─────────────────────────────────────────────────────────
  return (
    <figure className="w-full max-w-4xl mx-auto">
      <div
        className="relative rounded-2xl overflow-hidden border border-white/10 bg-black/80 shadow-2xl shadow-black/50"
        style={{ aspectRatio: "16 / 9" }}
      >
        <video
          ref={videoRef}
          src={src}
          poster={poster}
          preload="metadata"
          autoPlay={!reduced}
          loop
          muted
          playsInline
          controls
          aria-label="Quorus product walkthrough"
          className="absolute inset-0 w-full h-full object-cover"
        />
      </div>
      {caption ? (
        <figcaption className="text-center text-xs text-white/45 font-mono mt-3">
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );
}
