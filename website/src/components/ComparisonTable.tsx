import { motion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * ComparisonTable — three-vendor feature matrix rendered on cream.
 * Quorus column is visually highlighted with a thin accent border + tint.
 *
 * Table-only: header copy lives in <ComparisonBand>. Mobile pattern mirrors
 * <PricingTable>: horizontal scroll, sticky first column.
 */

type Vendor = "quorus" | "langgraph" | "crewai";

type CellValue = "yes" | "no" | string;

interface FeatureRow {
  feature: string;
  quorus: CellValue;
  langgraph: CellValue;
  crewai: CellValue;
}

const ROWS: FeatureRow[] = [
  {
    feature: "Cross-vendor (Claude + GPT + Gemini in one swarm)",
    quorus: "yes",
    langgraph: "partial",
    crewai: "no",
  },
  {
    feature: "MCP-native server",
    quorus: "yes",
    langgraph: "no",
    crewai: "no",
  },
  {
    feature: "Distributed locks (atomic file claim)",
    quorus: "yes",
    langgraph: "no",
    crewai: "no",
  },
  {
    feature: "Real-time SSE state",
    quorus: "yes",
    langgraph: "partial",
    crewai: "no",
  },
  {
    feature: "Self-hostable",
    quorus: "yes",
    langgraph: "yes",
    crewai: "yes",
  },
  { feature: "Python", quorus: "yes", langgraph: "yes", crewai: "yes" },
  {
    feature: "TypeScript SDK",
    quorus: "planned",
    langgraph: "yes",
    crewai: "no",
  },
  { feature: "License", quorus: "MIT", langgraph: "MIT", crewai: "MIT" },
  {
    feature: "Setup time",
    quorus: "<30s",
    langgraph: "~10min",
    crewai: "~5min",
  },
  {
    feature: "Production coordination",
    quorus: "yes",
    langgraph: "partial",
    crewai: "partial",
  },
];

const COLUMNS: ReadonlyArray<{ key: Vendor; label: string }> = [
  { key: "quorus", label: "Quorus" },
  { key: "langgraph", label: "LangGraph" },
  { key: "crewai", label: "CrewAI" },
];

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

function DashMark() {
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
        d="M4 8h8"
        stroke="var(--color-text-on-cream-muted)"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
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
        <DashMark />
      </span>
    );
  }
  return (
    <span
      className="font-mono text-[12px]"
      style={{ color: "var(--color-text-on-cream-secondary)" }}
    >
      {value}
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
        className="w-full min-w-[640px] border-collapse"
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
                minWidth: 260,
              }}
            >
              Feature
            </th>
            {COLUMNS.map((col) => {
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
                    minWidth: 140,
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
          {ROWS.map((row, rIdx) => {
            const isLast = rIdx === ROWS.length - 1;
            const dividerStyle = isLast
              ? undefined
              : "1px solid var(--color-border-light)";
            return (
              <tr key={row.feature}>
                <th
                  scope="row"
                  className="sticky left-0 z-10 px-5 py-3.5 text-left text-[13px]"
                  style={{
                    color: "var(--color-text-on-cream)",
                    fontFamily: "var(--font-sans)",
                    fontWeight: 500,
                    backgroundColor: "var(--color-cream)",
                    borderBottom: dividerStyle,
                  }}
                >
                  {row.feature}
                </th>
                {COLUMNS.map((col) => {
                  const isQuorus = col.key === "quorus";
                  const value = row[col.key];
                  const isLastRow = isLast;
                  return (
                    <td
                      key={col.key}
                      className="px-5 py-3.5 text-center"
                      style={{
                        borderBottom: isQuorus
                          ? isLastRow
                            ? "1.5px solid var(--color-accent)"
                            : "1px solid var(--color-border-light)"
                          : dividerStyle,
                        backgroundColor: isQuorus
                          ? "rgba(13,77,74,0.04)"
                          : undefined,
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
