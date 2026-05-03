import { useEffect, useRef, useState } from "react";

/**
 * AsciinemaPlayer — lazy-loaded wrapper around the npm `asciinema-player`
 * package. The bundle (and CSS) is imported only after the figure scrolls
 * within `rootMargin: "200px 0px"` of the viewport, keeping the initial
 * page bundle slim.
 *
 * Renders a `<figure role="region" aria-label="Quorus demo terminal">` with
 * a `<figcaption>` fallback so the band remains accessible (and renders
 * something) while the player is fetching or if the package fails to load.
 */
interface AsciinemaPlayerProps {
  /** URL of the asciicast v2 file (served from /public). */
  castUrl: string;
  /** Auto-play once the player mounts. */
  autoPlay?: boolean;
  /** Loop when the cast ends. */
  loop?: boolean;
  /** Optional cols/rows overrides for the player. */
  cols?: number;
  rows?: number;
  /** Idle time limit (seconds) before the player skips silence. */
  idleTimeLimit?: number;
  /** Caption rendered for screen readers and as a no-script fallback. */
  caption?: string;
}

interface AsciinemaCreatePlayerOptions {
  autoPlay?: boolean;
  loop?: boolean;
  cols?: number;
  rows?: number;
  idleTimeLimit?: number;
}

interface AsciinemaModule {
  create: (
    castUrl: string,
    target: HTMLElement,
    options?: AsciinemaCreatePlayerOptions,
  ) => { dispose?: () => void } | undefined;
}

export default function AsciinemaPlayer({
  castUrl,
  autoPlay = true,
  loop = true,
  cols,
  rows,
  idleTimeLimit = 2,
  caption = "Quorus demo terminal — placeholder recording.",
}: AsciinemaPlayerProps): JSX.Element {
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const targetRef = useRef<HTMLDivElement | null>(null);
  const [shouldLoad, setShouldLoad] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  // Stage 1: observe and flip `shouldLoad` once we're 200px from the
  // viewport. We keep the observer cheap (single ref, single threshold).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const node = wrapperRef.current;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") {
      // Older runtimes / some test envs — eagerly load and bail.
      setShouldLoad(true);
      return;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setShouldLoad(true);
            obs.disconnect();
            break;
          }
        }
      },
      { rootMargin: "200px 0px" },
    );
    obs.observe(node);
    return () => obs.disconnect();
  }, []);

  // Stage 2: dynamic import of the player only after intersection.
  useEffect(() => {
    if (!shouldLoad) return;
    if (typeof window === "undefined") return;
    let disposed = false;
    let player: { dispose?: () => void } | undefined;

    (async () => {
      try {
        // CSS is fetched alongside the JS chunk, but kept inside the dynamic
        // chunk so the initial bundle stays clean.
        await import("asciinema-player/dist/bundle/asciinema-player.css");
        const mod =
          (await import("asciinema-player")) as unknown as AsciinemaModule;
        if (disposed) return;
        const target = targetRef.current;
        if (!target) return;
        player = mod.create(castUrl, target, {
          autoPlay,
          loop,
          cols,
          rows,
          idleTimeLimit,
        });
      } catch (err) {
        if (!disposed) {
          // Keep the figcaption as the visible fallback — don't surface
          // anything noisier than that to the user.
          console.warn("AsciinemaPlayer: failed to load", err);
          setLoadFailed(true);
        }
      }
    })();

    return () => {
      disposed = true;
      try {
        player?.dispose?.();
      } catch {
        // ignore — best-effort cleanup
      }
    };
  }, [shouldLoad, castUrl, autoPlay, loop, cols, rows, idleTimeLimit]);

  return (
    <figure
      ref={wrapperRef}
      role="region"
      aria-label="Quorus demo terminal"
      style={{
        margin: 0,
        borderRadius: 12,
        overflow: "hidden",
        border: "1px solid var(--color-border-dark)",
        backgroundColor: "var(--color-ink-2)",
      }}
    >
      <div
        ref={targetRef}
        data-testid="asciinema-target"
        style={{
          minHeight: 220,
          width: "100%",
          backgroundColor: "var(--color-ink-2)",
        }}
      />
      <figcaption
        className="px-4 py-2 text-[12px]"
        style={{
          color: "var(--color-text-on-ink-muted)",
          fontFamily: "var(--font-mono)",
          letterSpacing: "0.04em",
          borderTop: "1px solid var(--color-border-dark)",
        }}
      >
        {loadFailed
          ? "Demo terminal unavailable — run quorus init locally to see it live."
          : caption}
      </figcaption>
    </figure>
  );
}
