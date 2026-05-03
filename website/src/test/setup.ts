import "@testing-library/jest-dom";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// Stub IntersectionObserver — jsdom doesn't ship one, and AsciinemaPlayer
// uses it to gate the dynamic import. We provide a noop that never fires
// `isIntersecting`, which means the lazy bundle is NEVER imported in
// tests (the figure + figcaption still render, which is what the suite
// actually asserts).
class IntersectionObserverStub {
  observe(): void {
    /* noop */
  }
  unobserve(): void {
    /* noop */
  }
  disconnect(): void {
    /* noop */
  }
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
  // The real DOM API exposes these — provide stable values so any code
  // that introspects them doesn't blow up.
  root: Element | null = null;
  rootMargin = "0px";
  thresholds: ReadonlyArray<number> = [0];
}

// Assigning to globalThis here covers both `window.IntersectionObserver`
// and the bare global reference used by some libraries.
Object.defineProperty(globalThis, "IntersectionObserver", {
  writable: true,
  configurable: true,
  value: IntersectionObserverStub,
});

// matchMedia — Framer Motion's `useReducedMotion` reads it. jsdom doesn't.
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }),
  });
}

afterEach(() => {
  cleanup();
});
