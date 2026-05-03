import { useMemo } from "react";
import { useReducedMotion } from "framer-motion";

/**
 * BrainSynapses — transparent SVG overlay layered on top of
 * /public/stitch/brain-scene.webp inside HeroLight's right column.
 *
 * Coordinates are in the same 776×700 viewBox as the source image. Nodes are
 * placed at visible "synapse-bump" hotspots on the rendered brain (frontal
 * lobe, parietal ridge, temporal sulci, occipital, cerebellum). The lower-
 * left area is intentionally avoided — that's where the orchestrator
 * terminal panel sits in the underlying image.
 *
 * Each node renders as a small filled dot plus an outer pulse ring that
 * rides a 3.4s loop staggered by index. Edges connect any two nodes within
 * 180px (viewBox units) Euclidean distance and shimmer with a dashed offset
 * loop, evoking signal flow without distracting from the headline copy on
 * the left column.
 *
 * Honors prefers-reduced-motion: in that mode dots render at full opacity,
 * edges render at full stroke, no pulses, no shimmer.
 *
 * The container is `pointer-events-none` so it never intercepts clicks on
 * the underlying hero composition.
 */

type Node = { x: number; y: number };

// 14 active synapse nodes, anatomically placed over the brain in
// brain-scene.webp. Frontal cluster (upper-left of brain), parietal crown,
// temporal lobe, occipital, cerebellum. All inside the 776×700 viewBox.
const NODES: readonly Node[] = [
  { x: 305, y: 110 }, // frontal, upper
  { x: 380, y: 90 }, // frontal-parietal junction (top crown)
  { x: 460, y: 110 }, // parietal, upper
  { x: 540, y: 145 }, // parietal-occipital
  { x: 615, y: 200 }, // occipital, upper
  { x: 245, y: 175 }, // frontal, mid
  { x: 360, y: 200 }, // central sulcus / motor cortex
  { x: 470, y: 230 }, // parietal, mid
  { x: 580, y: 285 }, // occipital, mid
  { x: 305, y: 290 }, // temporal-frontal hub (visible bright bump)
  { x: 425, y: 320 }, // central temporal hub (the brightest sphere)
  { x: 530, y: 380 }, // posterior temporal
  { x: 620, y: 420 }, // cerebellum upper
  { x: 600, y: 520 }, // cerebellum lower
] as const;

// Edges: pairs of node indices with distance under threshold. Computed once
// at module load so the render path stays cheap.
const EDGE_DISTANCE_THRESHOLD = 180;

type Edge = { a: number; b: number };

function computeEdges(nodes: readonly Node[], threshold: number): Edge[] {
  const edges: Edge[] = [];
  for (let i = 0; i < nodes.length; i += 1) {
    for (let j = i + 1; j < nodes.length; j += 1) {
      const a = nodes[i]!;
      const b = nodes[j]!;
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist <= threshold) edges.push({ a: i, b: j });
    }
  }
  return edges;
}

const DOT_RADIUS = 3;
const PULSE_DURATION_S = 3.4;
const PULSE_STAGGER_S = 0.4;
const EDGE_SHIMMER_DURATION_S = 8;

export default function BrainSynapses(): JSX.Element {
  const prefersReduced = useReducedMotion();
  const edges = useMemo(() => computeEdges(NODES, EDGE_DISTANCE_THRESHOLD), []);

  return (
    <svg
      aria-hidden
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox="0 0 776 700"
      preserveAspectRatio="xMidYMid meet"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      {/* Edges first so dots render on top of any line crossings */}
      <g>
        {edges.map((edge, i) => {
          const a = NODES[edge.a]!;
          const b = NODES[edge.b]!;
          return (
            <line
              key={`edge-${i}`}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke="var(--color-accent-on-ink)"
              strokeOpacity={prefersReduced ? 0.45 : 0.25}
              strokeWidth={0.8}
              strokeDasharray="4 6"
              strokeLinecap="round"
            >
              {prefersReduced ? null : (
                <animate
                  attributeName="stroke-dashoffset"
                  from="0"
                  to="-100"
                  dur={`${EDGE_SHIMMER_DURATION_S}s`}
                  repeatCount="indefinite"
                />
              )}
            </line>
          );
        })}
      </g>

      {/* Pulse rings — drawn behind the solid dots */}
      {!prefersReduced && (
        <g>
          {NODES.map((node, i) => {
            const begin = `${(i * PULSE_STAGGER_S).toFixed(2)}s`;
            return (
              <circle
                key={`pulse-${i}`}
                cx={node.x}
                cy={node.y}
                r={5}
                fill="none"
                stroke="var(--color-accent-on-ink)"
                strokeWidth={1}
                opacity={0}
              >
                <animate
                  attributeName="r"
                  values="5;14"
                  dur={`${PULSE_DURATION_S}s`}
                  begin={begin}
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="opacity"
                  values="0.6;0"
                  dur={`${PULSE_DURATION_S}s`}
                  begin={begin}
                  repeatCount="indefinite"
                />
              </circle>
            );
          })}
        </g>
      )}

      {/* Active dots */}
      <g>
        {NODES.map((node, i) => (
          <circle
            key={`dot-${i}`}
            cx={node.x}
            cy={node.y}
            r={DOT_RADIUS}
            fill="var(--color-accent-on-ink)"
            opacity={prefersReduced ? 1 : 0.95}
          />
        ))}
      </g>
    </svg>
  );
}
