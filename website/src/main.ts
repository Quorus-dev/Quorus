import "./site.css";

declare const __QUORUS_VERSION__: string; // from ../pyproject.toml at build time (vite.config.ts)

// Copy a command to the clipboard.
for (const button of document.querySelectorAll<HTMLButtonElement>("button[data-copy]")) {
  button.addEventListener("click", async () => {
    const source = document.getElementById(button.dataset.copy ?? "");
    if (!source?.textContent) return;
    try {
      await navigator.clipboard.writeText(source.textContent);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Select and copy";
    }
    setTimeout(() => (button.textContent = "Copy"), 2000);
  });
}

// The package version in the footer, read from pyproject.toml when the site is built.
const version = document.getElementById("version");
if (version && __QUORUS_VERSION__) version.textContent = `v${__QUORUS_VERSION__}`;

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

  // The hero's second beat, the Quorum stage (three.js, ~700 KB): desktop widths only,
  // never on a data-saver connection, and only once the page has loaded, gone idle and
  // the band itself is scrolled into view. Until then (and on phones) its still shows.
  const stageHost = document.getElementById("stageHost");
  const saveData = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection?.saveData;
  if (stageHost && matchMedia("(min-width: 960px)").matches && !saveData) {
    window.addEventListener("load", () => {
      const near = new IntersectionObserver((entries) => {
        if (!entries.some((e) => e.isIntersecting)) return;
        near.disconnect();
        const go = () => void import("./stage-demo.ts").then((m) => m.mountStage(stageHost)).catch(() => undefined);
        if ("requestIdleCallback" in window) requestIdleCallback(go, { timeout: 3000 });
        else setTimeout(go, 400);
      }, { threshold: 0.3 });
      near.observe(stageHost);
    });
  }
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
