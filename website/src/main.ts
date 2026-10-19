import "./site.css";
import "./copy.ts";

// The demo starts after the page has loaded and gone idle, so decoding it never
// competes with first paint (the poster is the first paint). With reduced motion
// it doesn't play by itself: the poster stays, with controls.
const demo = document.getElementById("demo") as HTMLVideoElement | null;
if (demo) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    demo.controls = true;
  } else {
    const start = () => void demo.play().catch(() => (demo.controls = true));
    window.addEventListener("load", () =>
      "requestIdleCallback" in window ? requestIdleCallback(start, { timeout: 2000 }) : setTimeout(start, 500),
    );
  }
}

// Reveal sections as they scroll into view: transform/opacity only, and only when
// the browser doesn't ask for reduced motion. Without JS everything is simply visible.
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
if (!reduce && "IntersectionObserver" in window) {
  document.documentElement.classList.add("js");
  // The hero replay, the scroll story and the micro-interactions load after first
  // paint, as their own chunk; with reduced motion none of it loads and the hero
  // shows the finished run (see the static markup in index.html and site.css).
  window.addEventListener("load", () => void import("./motion.ts").then((m) => m.start()));
  const seen = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add("in");
        seen.unobserve(entry.target);
      }
    },
    { rootMargin: "0px 0px -8% 0px" },
  );
  for (const el of document.querySelectorAll(".reveal")) seen.observe(el);
}

// Card images (crops of the demo run) load once the page has loaded and gone idle,
// so on a slow connection they never share bandwidth with the hero. Without JS,
// the <noscript> copies show instead.
const late = () => {
  for (const img of document.querySelectorAll<HTMLImageElement>("img[data-src]")) {
    img.src = img.dataset.src ?? "";
    img.removeAttribute("data-src");
  }
};
window.addEventListener("load", () =>
  "requestIdleCallback" in window ? requestIdleCallback(late, { timeout: 1500 }) : setTimeout(late, 300),
);
