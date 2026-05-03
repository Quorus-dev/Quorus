import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ComparisonTable from "../ComparisonTable";
import { COMPARISON_ROWS } from "../../data/cross_harness_copy";

// Escape a literal string for safe inclusion in a RegExp source. The moat
// badge appends " New" to the social-grammar row's accessible name, so we
// can't anchor with `^` either — a substring match is what we want.
function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function getRowByFeature(feature: string): HTMLTableRowElement {
  const cell = screen.getByRole("rowheader", {
    name: new RegExp(escapeRegExp(feature), "i"),
  });
  const row = cell.closest("tr");
  if (!row) throw new Error(`row not found for feature: ${feature}`);
  return row;
}

describe("ComparisonTable", () => {
  it("renders all six capability rows", () => {
    render(<ComparisonTable />);
    expect(COMPARISON_ROWS).toHaveLength(6);
    for (const row of COMPARISON_ROWS) {
      // Each feature label is a row-header cell.
      expect(
        screen.getByRole("rowheader", {
          name: new RegExp(escapeRegExp(row.feature), "i"),
        }),
      ).toBeInTheDocument();
    }
  });

  it("marks Quorus as Yes and every competitor as No on the social-grammar row", () => {
    render(<ComparisonTable />);
    const moatRow = getRowByFeature("Social grammar verbs");
    const yesIcons = moatRow.querySelectorAll('[aria-label="Yes"]');
    const noIcons = moatRow.querySelectorAll('[aria-label="No"]');
    // Exactly one ✓ (Quorus) and four ✗ (Devin, OpenAgents, TAP, AutoGen).
    expect(yesIcons).toHaveLength(1);
    expect(noIcons).toHaveLength(4);
  });

  it("includes an Apache-2.0 protocol row", () => {
    render(<ComparisonTable />);
    expect(
      screen.getByRole("rowheader", {
        name: /Apache-2\.0 protocol/i,
      }),
    ).toBeInTheDocument();
  });

  it('flags the social-grammar row with data-emphasis="moat"', () => {
    render(<ComparisonTable />);
    const moatRow = getRowByFeature("Social grammar verbs");
    expect(moatRow.getAttribute("data-emphasis")).toBe("moat");
    // Sanity: it also carries the visual class hook.
    expect(moatRow.className).toMatch(/comparison-row-moat/);
  });
});
