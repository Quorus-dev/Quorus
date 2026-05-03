import { motion, useReducedMotion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * BentoCodeSync — a small file tree (4 nodes) with diff bars next to each
 * row. One bar grows on the loop to suggest a live diff being captured.
 */
export default function BentoCodeSync() {
  const prefersReduced = useReducedMotion();

  // Tree rows: depth controls left indent, kind switches between folder
  // and file glyphs. Diff = +added / -removed per row.
  const rows: ReadonlyArray<{
    label: string;
    depth: number;
    kind: "folder" | "file";
    added: number;
    removed: number;
    pulse?: boolean;
  }> = [
    { label: "src", depth: 0, kind: "folder", added: 0, removed: 0 },
    { label: "auth.py", depth: 1, kind: "file", added: 12, removed: 4 },
    {
      label: "routes.py",
      depth: 1,
      kind: "file",
      added: 28,
      removed: 9,
      pulse: true,
    },
    { label: "tests/", depth: 1, kind: "folder", added: 6, removed: 0 },
  ];

  // Bar width per +1 line of diff.
  const SCALE = 1.6;

  return (
    <svg
      viewBox="0 0 200 200"
      width="100%"
      height="100%"
      role="img"
      aria-label="A small file tree with diff bars showing added and removed lines per file"
      preserveAspectRatio="xMidYMid meet"
    >
      {/* Tree spine — the vertical line connecting child rows. */}
      <line
        x1="20"
        y1="42"
        x2="20"
        y2="158"
        stroke="var(--color-text-on-ink-secondary)"
        strokeWidth="1"
        opacity="0.35"
      />

      {rows.map((row, i) => {
        const y = 32 + i * 36;
        const xLabel = 14 + row.depth * 18;
        const xBar = 100;

        return (
          <g key={row.label}>
            {/* Elbow connector for child rows */}
            {row.depth > 0 && (
              <path
                d={`M 20 ${y - 4} L 20 ${y + 6} L ${xLabel - 4} ${y + 6}`}
                fill="none"
                stroke="var(--color-text-on-ink-secondary)"
                strokeWidth="1"
                opacity="0.35"
              />
            )}

            {/* Glyph: folder = chevron, file = small box */}
            {row.kind === "folder" ? (
              <path
                d={`M ${xLabel} ${y + 2} L ${xLabel + 5} ${y + 6} L ${xLabel} ${y + 10}`}
                fill="none"
                stroke="var(--color-text-on-ink-secondary)"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            ) : (
              <rect
                x={xLabel}
                y={y + 1}
                width="9"
                height="11"
                rx="1.5"
                fill="none"
                stroke="var(--color-text-on-ink-secondary)"
                strokeWidth="1.3"
              />
            )}

            {/* Filename */}
            <text
              x={xLabel + 14}
              y={y + 11}
              fontSize="11"
              fontFamily="JetBrains Mono, monospace"
              fill={
                row.pulse
                  ? "var(--color-text-on-ink)"
                  : "var(--color-text-on-ink-secondary)"
              }
              opacity={row.pulse ? 1 : 0.85}
            >
              {row.label}
            </text>

            {/* Diff bars — additions on top of removals, segmented like GitHub. */}
            {row.added + row.removed > 0 && (
              <g transform={`translate(${xBar} ${y + 3})`}>
                {/* Removed (muted) bar */}
                <rect
                  x="0"
                  y="0"
                  width={Math.max(row.removed * SCALE, row.removed > 0 ? 4 : 0)}
                  height="8"
                  rx="2"
                  fill="var(--color-text-on-ink-muted)"
                  opacity="0.55"
                />
                {/* Added (accent) bar — animated for the pulse row. */}
                <motion.rect
                  x={Math.max(row.removed * SCALE, row.removed > 0 ? 4 : 0) + 3}
                  y="0"
                  height="8"
                  rx="2"
                  fill="var(--color-accent-on-ink)"
                  initial={false}
                  animate={
                    row.pulse && !prefersReduced
                      ? {
                          width: [
                            row.added * SCALE * 0.45,
                            row.added * SCALE,
                            row.added * SCALE * 0.45,
                          ],
                        }
                      : { width: row.added * SCALE }
                  }
                  transition={
                    row.pulse && !prefersReduced
                      ? { duration: 3.2, repeat: Infinity, ease: EASE }
                      : { duration: 0.4, ease: EASE }
                  }
                />

                {/* Counts */}
                <text
                  x="0"
                  y="22"
                  fontSize="9"
                  fontFamily="JetBrains Mono, monospace"
                  fill="var(--color-accent-on-ink)"
                  opacity="0.85"
                >
                  +{row.added}
                </text>
                {row.removed > 0 && (
                  <text
                    x="22"
                    y="22"
                    fontSize="9"
                    fontFamily="JetBrains Mono, monospace"
                    fill="var(--color-text-on-ink-muted)"
                    opacity="0.85"
                  >
                    −{row.removed}
                  </text>
                )}
              </g>
            )}
          </g>
        );
      })}
    </svg>
  );
}
