import type { JSX } from "react";
import {
  OS_PRIMITIVES,
  OS_PRIMITIVES_COPY,
  type PrimitiveStatus,
} from "../data/os_primitives_copy";

/**
 * OsPrimitivesTable — the eight-primitive roadmap band.
 *
 * Sits between the cream Hero and the dark CrossHarnessBand to anchor the
 * "agent-native operating system" framing introduced in the headline. The
 * cross-vendor band that follows is the deep dive on the LIVE Coordination
 * primitive — this band names all eight so visitors understand the surface
 * area Quorus is building, not just what ships today.
 *
 * Surface theme: dark, matching the rest of the after-hero stack so the
 * cream→dark transition only happens once on the page.
 *
 * Self-contained, no props, no external state. Visible strings are sourced
 * from `src/data/os_primitives_copy.ts` so the regression suite can assert
 * verbatim matches.
 */

// Status pill palette. LIVE leans on the same accent teal used for shipped
// surfaces elsewhere; the day-bucketed roadmap rows desaturate progressively
// so the eye groups them as "future" without needing to read the label.
function statusPalette(status: PrimitiveStatus): {
  color: string;
  bg: string;
  border: string;
} {
  if (status === "LIVE") {
    return {
      color: "var(--color-accent-on-ink)",
      bg: "rgba(94,179,168,0.12)",
      border: "rgba(94,179,168,0.45)",
    };
  }
  // Roadmap rows share a quieter palette; only the label distinguishes
  // them. Keeping a single muted look avoids a status-bar effect that
  // would over-promise three different colors of "soon".
  return {
    color: "var(--color-text-on-ink-secondary)",
    bg: "rgba(255,255,255,0.04)",
    border: "var(--color-border-dark-strong)",
  };
}

function StatusPill({ status }: { status: PrimitiveStatus }): JSX.Element {
  const palette = statusPalette(status);
  return (
    <span
      className="inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px]"
      style={{
        color: palette.color,
        backgroundColor: palette.bg,
        border: `1px solid ${palette.border}`,
        fontFamily: "var(--font-mono)",
        letterSpacing: "0.12em",
      }}
    >
      {status}
    </span>
  );
}

export default function OsPrimitivesTable(): JSX.Element {
  return (
    <section
      data-theme="dark"
      aria-labelledby="os-primitives-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: "var(--color-ink)" }}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 60% 40% at 50% 20%, rgba(94,179,168,0.08), transparent 70%)",
        }}
      />

      <div className="relative mx-auto max-w-5xl px-6 py-24 md:py-28">
        <div className="mx-auto max-w-3xl text-center">
          <p
            className="text-[11px] uppercase"
            style={{
              color: "var(--color-accent-on-ink)",
              fontFamily: "var(--font-mono)",
              letterSpacing: "0.22em",
            }}
          >
            {OS_PRIMITIVES_COPY.eyebrow}
          </p>
          <h2
            id="os-primitives-heading"
            className="mt-4 text-balance"
            style={{
              color: "var(--color-text-on-ink)",
              fontFamily: "var(--font-sans)",
              fontSize: "clamp(28px, 3.4vw, 44px)",
              fontWeight: 600,
              lineHeight: 1.1,
              letterSpacing: "-0.02em",
            }}
          >
            {OS_PRIMITIVES_COPY.headline}
          </h2>
          <p
            className="mx-auto mt-5 max-w-2xl text-pretty"
            style={{
              color: "var(--color-text-on-ink-secondary)",
              fontFamily: "var(--font-sans)",
              fontSize: 16,
              lineHeight: 1.6,
            }}
          >
            {OS_PRIMITIVES_COPY.subline}
          </p>
        </div>

        <div
          className="mt-14 overflow-hidden rounded-xl"
          style={{
            border: "1px solid var(--color-border-dark-strong)",
            backgroundColor: "var(--color-ink-2)",
          }}
        >
          <table
            data-testid="os-primitives-table"
            className="w-full text-left"
            style={{ fontFamily: "var(--font-sans)" }}
          >
            <thead>
              <tr
                style={{
                  borderBottom: "1px solid var(--color-border-dark-strong)",
                  color: "var(--color-text-on-ink-muted)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  letterSpacing: "0.18em",
                }}
              >
                <th scope="col" className="px-5 py-3 uppercase">
                  Primitive
                </th>
                <th scope="col" className="px-5 py-3 uppercase">
                  What it gives agents
                </th>
                <th scope="col" className="px-5 py-3 text-right uppercase">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {OS_PRIMITIVES.map((row, i) => (
                <tr
                  key={row.primitive}
                  style={{
                    borderTop:
                      i === 0
                        ? undefined
                        : "1px solid var(--color-border-dark)",
                  }}
                >
                  <th
                    scope="row"
                    className="px-5 py-4 text-[15px]"
                    style={{
                      color: "var(--color-text-on-ink)",
                      fontWeight: 600,
                      letterSpacing: "-0.005em",
                    }}
                  >
                    {row.primitive}
                  </th>
                  <td
                    className="px-5 py-4 text-[14px]"
                    style={{
                      color: "var(--color-text-on-ink-secondary)",
                      lineHeight: 1.5,
                    }}
                  >
                    {row.description}
                  </td>
                  <td className="px-5 py-4 text-right">
                    <StatusPill status={row.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
