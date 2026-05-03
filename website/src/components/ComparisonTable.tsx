import { motion } from "framer-motion";
import {
  COMPARISON_COLUMNS,
  COMPARISON_ROWS,
  type CellValue,
  type ComparisonRow,
} from "../data/cross_harness_copy";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * ComparisonTable — five-vendor capability matrix rendered on cream.
 *
 * Quorus column is visually highlighted with a thin accent border + tint.
 * The "Social grammar verbs" row is the moat — it carries
 * `data-emphasis="moat"`, a left accent bar, and a "NEW" pill badge.
 *
 * Table-only: header copy lives in <ComparisonBand>. Mobile pattern mirrors
 * <PricingTable>: horizontal scroll, sticky first column.
 */

function CheckMark() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      aria-label="Yes"
      role="img"
    >
      <path
        d="M3.5 8.5l3 3 6-6"
        stroke="var(--color-accent)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CrossMark() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      aria-label="No"
      role="img"
    >
      <path
        d="M4 4l8 8M12 4l-8 8"
        stroke="var(--color-text-on-cream-muted)"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function PartialMark() {
  return (
    <span
      aria-label="Partial"
      role="img"
      className="font-mono text-[11.5px] uppercase tracking-[0.08em]"
      style={{ color: "var(--color-text-on-cream-secondary)" }}
    >
      partial
    </span>
  );
}

function Cell({ value }: { value: CellValue }) {
  if (value === "yes") {
    return (
      <span className="inline-flex items-center justify-center">
        <CheckMark />
      </span>
    );
  }
  if (value === "no") {
    return (
      <span className="inline-flex items-center justify-center">
        <CrossMark />
      </span>
    );
  }
  if (value === "partial") {
    return (
      <span className="inline-flex items-center justify-center">
        <PartialMark />
      </span>
    );
  }
  // Free-form strings (e.g., "—" for "not applicable") — neutral mono treatment.
  return (
    <span
      className="font-mono text-[12px]"
      style={{ color: "var(--color-text-on-cream-secondary)" }}
    >
      {value}
    </span>
  );
}

function MoatBadge() {
  return (
    <span
      className="ml-2 inline-flex items-center rounded-full px-2 py-[2px] font-mono text-[9.5px] uppercase tracking-[0.14em]"
      style={{
        backgroundColor: "var(--color-accent)",
        color: "var(--color-accent-on-ink)",
        fontWeight: 600,
        letterSpacing: "0.16em",
      }}
    >
      New
    </span>
  );
}

export default function ComparisonTable() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.15 }}
      transition={{ duration: 0.7, ease: EASE, delay: 0.15 }}
      className="relative mt-14 overflow-x-auto"
      style={{
        border: "1px solid var(--color-border-light-strong)",
        borderRadius: 12,
        backgroundColor: "var(--color-cream)",
      }}
    >
      <table
        className="w-full min-w-[720px] border-collapse"
        style={{ fontFamily: "var(--font-sans)" }}
      >
        <thead>
          <tr>
            <th
              scope="col"
              className="sticky left-0 z-10 px-5 py-4 text-left text-[11px] uppercase"
              style={{
                color: "var(--color-text-on-cream-muted)",
                fontFamily: "var(--font-mono)",
                letterSpacing: "0.18em",
                backgroundColor: "var(--color-cream)",
                borderBottom: "1px solid var(--color-border-light-strong)",
                minWidth: 240,
              }}
            >
              Capability
            </th>
            {COMPARISON_COLUMNS.map((col) => {
              const isQuorus = col.key === "quorus";
              return (
                <th
                  key={col.key}
                  scope="col"
                  className="px-5 py-4 text-center text-[11px] uppercase"
                  style={{
                    color: isQuorus
                      ? "var(--color-accent)"
                      : "var(--color-text-on-cream-muted)",
                    fontFamily: "var(--font-mono)",
                    letterSpacing: "0.18em",
                    fontWeight: 600,
                    borderBottom: "1px solid var(--color-border-light-strong)",
                    minWidth: 110,
                    backgroundColor: isQuorus
                      ? "rgba(13,77,74,0.04)"
                      : undefined,
                    borderLeft: isQuorus
                      ? "1.5px solid var(--color-accent)"
                      : undefined,
                    borderRight: isQuorus
                      ? "1.5px solid var(--color-accent)"
                      : undefined,
                    borderTop: isQuorus
                      ? "1.5px solid var(--color-accent)"
                      : undefined,
                  }}
                >
                  {col.label}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {COMPARISON_ROWS.map((row, rIdx) => {
            const isLast = rIdx === COMPARISON_ROWS.length - 1;
            const dividerStyle = isLast
              ? undefined
              : "1px solid var(--color-border-light)";
            const moat = row.highlight === true;
            const rowBg = moat ? "rgba(94,179,168,0.06)" : undefined;
            return (
              <tr
                key={row.feature}
                data-emphasis={moat ? "moat" : undefined}
                className={moat ? "comparison-row-moat" : undefined}
                style={moat ? { backgroundColor: rowBg } : undefined}
              >
                <th
                  scope="row"
                  className="sticky left-0 z-10 px-5 py-3.5 text-left text-[13px]"
                  style={{
                    color: "var(--color-text-on-cream)",
                    fontFamily: "var(--font-sans)",
                    fontWeight: moat ? 600 : 500,
                    backgroundColor: rowBg ?? "var(--color-cream)",
                    borderBottom: dividerStyle,
                    borderLeft: moat
                      ? "3px solid var(--color-accent)"
                      : undefined,
                  }}
                >
                  <span className="inline-flex items-center">
                    {row.feature}
                    {moat ? <MoatBadge /> : null}
                  </span>
                </th>
                {COMPARISON_COLUMNS.map((col) => {
                  const isQuorus = col.key === "quorus";
                  const value = row[
                    col.key as keyof ComparisonRow
                  ] as CellValue;
                  return (
                    <td
                      key={col.key}
                      className="px-5 py-3.5 text-center"
                      style={{
                        borderBottom: isQuorus
                          ? isLast
                            ? "1.5px solid var(--color-accent)"
                            : "1px solid var(--color-border-light)"
                          : dividerStyle,
                        backgroundColor: isQuorus
                          ? "rgba(13,77,74,0.04)"
                          : rowBg,
                        borderLeft: isQuorus
                          ? "1.5px solid var(--color-accent)"
                          : undefined,
                        borderRight: isQuorus
                          ? "1.5px solid var(--color-accent)"
                          : undefined,
                      }}
                    >
                      <Cell value={value} />
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </motion.div>
  );
}
