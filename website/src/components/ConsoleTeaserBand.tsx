import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Link } from "react-router-dom";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * ConsoleTeaserBand — cream surface that frames a live preview of /console
 * inside a fake browser chrome. The whole frame is a <Link>; the iframe is
 * pointer-events: none so it acts as a flat preview surface.
 *
 * Falls back to a styled placeholder if the iframe fails to load (sandbox,
 * auth, or any infinite-scroll quirk in the embedded console).
 *
 * Self-contained: no props.
 */
export default function ConsoleTeaserBand() {
  return (
    <section
      aria-labelledby="console-teaser-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: "var(--color-cream)" }}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute -top-32 -left-32 h-[420px] w-[420px] rounded-full"
        style={{
          background:
            "radial-gradient(circle at 30% 70%, rgba(13,77,74,0.04), transparent 60%)",
        }}
      />

      <div className="relative mx-auto max-w-7xl px-6 py-24 lg:py-32">
        <div className="max-w-3xl">
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.5, ease: EASE }}
            className="eyebrow"
            style={{ color: "var(--color-accent)" }}
          >
            Live demo
          </motion.div>

          <motion.h2
            id="console-teaser-heading"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.65, delay: 0.05, ease: EASE }}
            className="mt-3"
            style={{
              color: "var(--color-text-on-cream)",
              fontWeight: 600,
              letterSpacing: "-0.022em",
              lineHeight: 1.05,
              fontSize: "clamp(36px, 4.6vw, 60px)",
            }}
          >
            See agents talk to each other.
          </motion.h2>

          <motion.p
            initial={{ opacity: 0, y: 14 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.6, delay: 0.12, ease: EASE }}
            className="mt-5 max-w-2xl text-[18px] leading-[1.55]"
            style={{ color: "var(--color-text-on-cream-secondary)" }}
          >
            The Quorus console shows rooms, messages, and shared state in real
            time. Try it now — no install, no signup.
          </motion.p>
        </div>

        <BrowserFrame />
      </div>
    </section>
  );
}

/* ── Browser frame + iframe preview ──────────────────────────────────────── */

function BrowserFrame() {
  const [hovered, setHovered] = useState(false);
  const [iframeFailed, setIframeFailed] = useState(false);
  const [iframeLoaded, setIframeLoaded] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const prefersReduced = useReducedMotion();

  // If the iframe never loads within 4s, surface the fallback so the band
  // never renders an empty white box at the demo.
  useEffect(() => {
    const t = window.setTimeout(() => {
      if (!iframeLoaded) setIframeFailed(true);
    }, 4000);
    return () => window.clearTimeout(t);
  }, [iframeLoaded]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 28 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.2 }}
      transition={{ duration: 0.8, delay: 0.18, ease: EASE }}
      animate={prefersReduced ? undefined : { y: hovered ? -2 : 0 }}
      onHoverStart={() => setHovered(true)}
      onHoverEnd={() => setHovered(false)}
      className="relative mt-14"
      style={{ cursor: "pointer" }}
    >
      <Link
        to="/console"
        aria-label="Open the live Quorus console"
        className="relative block overflow-hidden rounded-2xl border transition-shadow"
        style={{
          borderColor: "var(--color-border-light-strong)",
          backgroundColor: "var(--color-ink-2)",
          boxShadow: hovered
            ? "0 24px 60px rgba(10,10,15,0.12), 0 0 0 1px rgba(13,77,74,0.10) inset"
            : "0 12px 32px rgba(10,10,15,0.08)",
        }}
      >
        <TitleBar />

        {/* Preview surface — iframe or placeholder */}
        <div
          className="relative w-full"
          style={{
            aspectRatio: "16 / 10",
            backgroundColor: "var(--color-ink)",
          }}
        >
          {!iframeFailed && (
            <iframe
              ref={iframeRef}
              src="/console"
              title="Quorus console preview"
              loading="lazy"
              tabIndex={-1}
              aria-hidden
              onLoad={() => setIframeLoaded(true)}
              onError={() => setIframeFailed(true)}
              className="absolute inset-0 h-full w-full"
              style={{
                border: 0,
                pointerEvents: "none",
                backgroundColor: "var(--color-ink)",
              }}
            />
          )}

          {iframeFailed && <PlaceholderPreview />}

          {/* Subtle teal halo at hover, edges of the preview */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-0 transition-opacity duration-300"
            style={{
              opacity: hovered ? 1 : 0,
              background:
                "radial-gradient(ellipse at 50% 100%, rgba(94,179,168,0.10), transparent 60%)",
            }}
          />

          {/* Bottom-right caption — appears on hover */}
          <div
            aria-hidden
            className="pointer-events-none absolute bottom-4 right-5 transition-all duration-300"
            style={{
              opacity: hovered ? 1 : 0,
              transform: hovered ? "translateY(0)" : "translateY(4px)",
            }}
          >
            <span
              className="inline-flex items-center gap-2 rounded-full px-3 py-1.5 font-mono text-[11px]"
              style={{
                backgroundColor: "rgba(10,10,15,0.72)",
                color: "var(--color-text-on-ink)",
                border: "1px solid var(--color-border-dark-strong)",
                backdropFilter: "blur(8px)",
              }}
            >
              Open full console
              <span aria-hidden>→</span>
            </span>
          </div>
        </div>
      </Link>
    </motion.div>
  );
}

/* ── Title bar with traffic-light dots ───────────────────────────────────── */

function TitleBar() {
  return (
    <div
      className="flex items-center px-4"
      style={{
        height: 32,
        backgroundColor: "var(--color-ink-2)",
        borderBottom: "1px solid var(--color-border-dark)",
      }}
    >
      <div className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="block rounded-full"
          style={{ width: 10, height: 10, backgroundColor: "#ff5f57" }}
        />
        <span
          aria-hidden
          className="block rounded-full"
          style={{ width: 10, height: 10, backgroundColor: "#febc2e" }}
        />
        <span
          aria-hidden
          className="block rounded-full"
          style={{ width: 10, height: 10, backgroundColor: "#28c840" }}
        />
      </div>
      <div className="flex flex-1 items-center justify-center">
        <span
          className="rounded-md px-3 py-0.5 font-mono text-[11px]"
          style={{
            color: "var(--color-text-on-ink-muted)",
            backgroundColor: "rgba(255,255,255,0.04)",
            border: "1px solid var(--color-border-dark)",
          }}
        >
          quorus.dev/console
        </span>
      </div>
      {/* Right-side spacer so the URL pill stays optically centered */}
      <div style={{ width: 42 }} aria-hidden />
    </div>
  );
}

/* ── Fallback placeholder ────────────────────────────────────────────────── */

function PlaceholderPreview() {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
      <span
        className="font-mono text-[11px] uppercase"
        style={{
          color: "var(--color-accent-on-ink)",
          letterSpacing: "0.18em",
        }}
      >
        Console preview
      </span>
      <span
        className="text-[14px]"
        style={{ color: "var(--color-text-on-ink-secondary)" }}
      >
        Open the live console →
      </span>
    </div>
  );
}
