import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import CrossHarnessBand from "../CrossHarnessBand";
import {
  CROSS_HARNESS_COPY,
  HARNESS_LABELS,
} from "../../data/cross_harness_copy";

// `asciinema-player` ships ESM that touches `document` at import time and
// pulls in CSS — neither plays well with jsdom. We mock both. The
// `IntersectionObserver` stub in `src/test/setup.ts` ensures the dynamic
// import inside AsciinemaPlayer never fires anyway, but mocking is still
// a good belt-and-braces guard for future edits.
vi.mock("asciinema-player", () => ({
  create: vi.fn(() => ({ dispose: vi.fn() })),
}));
vi.mock("asciinema-player/dist/bundle/asciinema-player.css", () => ({}));

describe("CrossHarnessBand", () => {
  it("renders all four harness labels in the install switcher", () => {
    render(<CrossHarnessBand />);
    const tablist = screen.getByRole("tablist", {
      name: /install quorus in your harness/i,
    });
    for (const label of HARNESS_LABELS) {
      // Each label appears exactly once in the tab strip.
      expect(within(tablist).getByText(label)).toBeInTheDocument();
    }
  });

  it("renders headline and subline strings sourced verbatim from cross_harness_copy.ts", () => {
    render(<CrossHarnessBand />);
    // Heading by role asserts the h2 carries the literal text.
    const heading = screen.getByRole("heading", {
      level: 2,
      name: CROSS_HARNESS_COPY.headline,
    });
    expect(heading).toBeInTheDocument();
    // Subline lookup is a direct text match — confirms the band reads the
    // constants module instead of inlining strings.
    expect(screen.getByText(CROSS_HARNESS_COPY.subline)).toBeInTheDocument();
  });

  it("mounts the AsciinemaPlayer figure with its accessible name", () => {
    render(<CrossHarnessBand />);
    const figure = screen.getByRole("region", {
      name: /quorus demo terminal/i,
    });
    expect(figure).toBeInTheDocument();
    // The internal target div is present (mock means no error from create).
    expect(
      figure.querySelector('[data-testid="asciinema-target"]'),
    ).toBeInTheDocument();
  });

  it("exposes a CTA pointing at /docs/quickstart with the verbatim label", () => {
    render(<CrossHarnessBand />);
    const cta = screen.getByTestId("cross-harness-cta");
    expect(cta).toHaveAttribute("href", "/docs/quickstart");
    expect(cta).toHaveTextContent(CROSS_HARNESS_COPY.ctaLabel);
  });
});
