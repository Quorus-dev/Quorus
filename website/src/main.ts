import "./site.css";

// The only script on the page: copy a command to the clipboard.
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
