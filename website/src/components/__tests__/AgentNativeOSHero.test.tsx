import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import HeroLight from "../HeroLight";
import OsPrimitivesTable from "../OsPrimitivesTable";
import { OS_HERO_COPY, OS_PRIMITIVES } from "../../data/os_primitives_copy";

// HeroRoom mounts a self-cycling timer-driven mock with internal state.
// It isn't under test here — stub it so the hero's headline + install +
// spec-CTA are the only things asserted on this surface.
vi.mock("../HeroRoom", () => ({
  default: () => <div data-testid="hero-room-stub" />,
}));

describe("AgentNativeOSHero — Plan v8 framing", () => {
  it("renders the agent-native OS headline as the page H1", () => {
    render(<HeroLight />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent(/agent-native operating system/i);
    expect(heading).toHaveTextContent(OS_HERO_COPY.headline);
  });

  it("renders the pipx install command as the install snippet", () => {
    render(<HeroLight />);
    // The install command renders inside a <code> with a typewriter loop;
    // the initial state is the FULL command string, so it's present at
    // first paint with no async wait.
    expect(
      screen.getByText(OS_HERO_COPY.installCmd, { selector: "code" }),
    ).toBeInTheDocument();
  });

  it("exposes a spec CTA pointing at /protocol with the verbatim label", () => {
    render(<HeroLight />);
    const cta = screen.getByTestId("hero-spec-cta");
    expect(cta).toHaveAttribute("href", "/protocol");
    expect(cta).toHaveTextContent(OS_HERO_COPY.specCtaLabel);
  });

  it("renders all eight OS primitives with their verbatim status labels", () => {
    render(<OsPrimitivesTable />);
    const table = screen.getByTestId("os-primitives-table");
    expect(table).toBeInTheDocument();

    // Defensive guard — if the data source changes shape, we want a
    // single failure message instead of eight cryptic ones.
    expect(OS_PRIMITIVES).toHaveLength(8);

    for (const row of OS_PRIMITIVES) {
      // Primitive name lands in a row header, description in the cell next
      // to it. Both must be present verbatim.
      expect(screen.getByText(row.primitive)).toBeInTheDocument();
      expect(screen.getByText(row.description)).toBeInTheDocument();
    }

    // Status labels repeat (LIVE x2, "30 days" x3, "90 days" x2,
    // "120 days" x1) — assert occurrence counts so a re-bucketing of
    // the roadmap fails loud.
    expect(screen.getAllByText("LIVE")).toHaveLength(2);
    expect(screen.getAllByText("30 days")).toHaveLength(3);
    expect(screen.getAllByText("90 days")).toHaveLength(2);
    expect(screen.getAllByText("120 days")).toHaveLength(1);
  });
});
