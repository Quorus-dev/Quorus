import { motion, useReducedMotion } from "framer-motion";

/**
 * Real-time SSE — a vertical event stream with rows that scroll up.
 * Each row is a synthetic SSE event with timestamp + event type.
 */
export function SseIllustration() {
  const prefersReduced = useReducedMotion();

  const events = [
    { ts: "14:23:41", type: "room.join", agent: "claude-1" },
    { ts: "14:23:43", type: "lock.acquire", agent: "claude-1" },
    { ts: "14:23:44", type: "state.update", agent: "cursor-2" },
    { ts: "14:23:46", type: "message.sent", agent: "codex-3" },
    { ts: "14:23:48", type: "lock.release", agent: "claude-1" },
    { ts: "14:23:51", type: "task.claim", agent: "cursor-2" },
  ];

  return (
    <svg
      viewBox="0 0 290 180"
      width="100%"
      height="100%"
      role="img"
      aria-label="Server-sent events stream with timestamped events"
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

      {/* Header strip */}
      <rect
        x="14"
        y="14"
        width="262"
        height="22"
        rx="14"
        fill="#0a0a0f"
        fillOpacity="0.025"
      />
      <text
        x="28"
        y="29"
        fontSize="9"
        fontFamily="JetBrains Mono, monospace"
        fill="#0a0a0f"
        opacity="0.55"
      >
        GET /events?room=dev-sprint
      </text>
      <g transform="translate(238 22)">
        <circle cx="0" cy="0" r="3" fill="#0d4d4a" />
        <text
          x="6"
          y="3"
          fontSize="8"
          fontFamily="JetBrains Mono, monospace"
          fill="#0d4d4a"
          opacity="0.85"
        >
          200 OK
        </text>
      </g>

      {/* Mask so rows fade out at the top edge of the stream area */}
      <defs>
        <linearGradient id="sse-fade" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#000" stopOpacity="0" />
          <stop offset="0.18" stopColor="#000" stopOpacity="1" />
          <stop offset="0.85" stopColor="#000" stopOpacity="1" />
          <stop offset="1" stopColor="#000" stopOpacity="0" />
        </linearGradient>
        <mask id="sse-stream-mask">
          <rect x="14" y="40" width="262" height="124" fill="url(#sse-fade)" />
        </mask>
      </defs>

      <g mask="url(#sse-stream-mask)">
        <motion.g
          animate={prefersReduced ? { y: 0 } : { y: [0, -120] }}
          transition={{
            duration: 9,
            repeat: prefersReduced ? 0 : Infinity,
            ease: "linear",
          }}
        >
          {/* Render twice so the loop seamless */}
          {[...events, ...events].map((evt, i) => {
            const y = 50 + i * 20;
            return (
              <g key={`${evt.ts}-${i}`}>
                {/* Timestamp */}
                <text
                  x="28"
                  y={y}
                  fontSize="8"
                  fontFamily="JetBrains Mono, monospace"
                  fill="#0a0a0f"
                  opacity="0.4"
                >
                  {evt.ts}
                </text>
                {/* Event dot */}
                <circle cx="80" cy={y - 3} r="2" fill="#0d4d4a" />
                {/* Event type */}
                <text
                  x="90"
                  y={y}
                  fontSize="9"
                  fontFamily="JetBrains Mono, monospace"
                  fill="#0a0a0f"
                  opacity="0.85"
                  fontWeight="500"
                >
                  {evt.type}
                </text>
                {/* Agent */}
                <text
                  x="180"
                  y={y}
                  fontSize="8"
                  fontFamily="JetBrains Mono, monospace"
                  fill="#0a0a0f"
                  opacity="0.45"
                >
                  · {evt.agent}
                </text>
              </g>
            );
          })}
        </motion.g>
      </g>
    </svg>
  );
}
