import { motion, useReducedMotion } from "framer-motion";

/**
 * MCP Native — three concentric rings labeled tool / resource / prompt.
 * A small token travels around the inner ring on a loop, suggesting protocol traffic.
 */
export function McpIllustration() {
  const prefersReduced = useReducedMotion();
  const cx = 145;
  const cy = 90;

  return (
    <svg
      viewBox="0 0 290 180"
      width="100%"
      height="100%"
      role="img"
      aria-label="Concentric rings depicting MCP protocol layers"
    >
      {/* Frame */}
      <rect
        x="14"
        y="14"
        width="262"
        height="152"
        rx="14"
        fill="none"
        stroke="#0a0a0f"
        strokeOpacity="0.1"
        strokeWidth="1"
      />

      {/* Outer ring — prompts */}
      <circle
        cx={cx}
        cy={cy}
        r="68"
        fill="none"
        stroke="#0a0a0f"
        strokeOpacity="0.15"
        strokeWidth="1"
        strokeDasharray="2 4"
      />
      <text
        x={cx + 56}
        y={cy - 50}
        fontSize="8"
        fontFamily="JetBrains Mono, monospace"
        fill="#0a0a0f"
        opacity="0.5"
      >
        prompts
      </text>

      {/* Mid ring — resources */}
      <circle
        cx={cx}
        cy={cy}
        r="48"
        fill="none"
        stroke="#0a0a0f"
        strokeOpacity="0.22"
        strokeWidth="1"
      />
      <text
        x={cx + 38}
        y={cy - 36}
        fontSize="8"
        fontFamily="JetBrains Mono, monospace"
        fill="#0a0a0f"
        opacity="0.55"
      >
        resources
      </text>

      {/* Inner ring — tools (animated traveling token) */}
      <circle
        cx={cx}
        cy={cy}
        r="28"
        fill="none"
        stroke="#0d4d4a"
        strokeOpacity="0.4"
        strokeWidth="1.2"
      />
      <text
        x={cx + 22}
        y={cy - 22}
        fontSize="8"
        fontFamily="JetBrains Mono, monospace"
        fill="#0d4d4a"
        opacity="0.75"
      >
        tools
      </text>

      {/* Token traveling around the inner ring */}
      {!prefersReduced && (
        <motion.circle
          r="3.5"
          fill="#0d4d4a"
          animate={{
            cx: [
              cx + 28,
              cx + 19.8,
              cx,
              cx - 19.8,
              cx - 28,
              cx - 19.8,
              cx,
              cx + 19.8,
              cx + 28,
            ],
            cy: [
              cy,
              cy + 19.8,
              cy + 28,
              cy + 19.8,
              cy,
              cy - 19.8,
              cy - 28,
              cy - 19.8,
              cy,
            ],
          }}
          transition={{
            duration: 6,
            repeat: Infinity,
            ease: "linear",
          }}
        />
      )}

      {/* Center — MCP core node */}
      <circle
        cx={cx}
        cy={cy}
        r="10"
        fill="#0a0a0f"
        stroke="#0d4d4a"
        strokeWidth="1.5"
      />
      <text
        x={cx}
        y={cy + 3}
        fontSize="8"
        fontFamily="JetBrains Mono, monospace"
        fontWeight="600"
        fill="#f5f1ea"
        textAnchor="middle"
      >
        mcp
      </text>

      {/* Tool list on the right */}
      <g transform="translate(220 60)">
        {["join_room", "claim_task", "send_message", "get_room_state"].map(
          (tool, i) => (
            <g key={tool} transform={`translate(0 ${i * 16})`}>
              <rect
                x="0"
                y="0"
                width="46"
                height="11"
                rx="2"
                fill="#0a0a0f"
                fillOpacity="0.04"
                stroke="#0a0a0f"
                strokeOpacity="0.1"
                strokeWidth="0.6"
              />
              <text
                x="3"
                y="8"
                fontSize="7"
                fontFamily="JetBrains Mono, monospace"
                fill="#0a0a0f"
                opacity="0.6"
              >
                {tool}
              </text>
            </g>
          ),
        )}
      </g>
    </svg>
  );
}
