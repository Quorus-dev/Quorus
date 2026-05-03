import { useMemo } from "react";
import { motion, useReducedMotion } from "framer-motion";

/**
 * NeuralSphere — a cinematic hero visual.
 *
 * A dense lattice of ~96 nodes arranged on the surface of a sphere via the
 * Fibonacci spiral, projected into 2D with depth-aware sizing and opacity.
 * Edges connect nearby nodes (Euclidean threshold). A subset of "active"
 * nodes are tinted with the accent color and pulse on a slow loop.
 *
 * The whole thing rotates very slowly (one revolution every ~60s). At rest
 * it's a dense node graph; in motion it gives the impression of a 3D system
 * thinking. Matches the dense circular graph in the Stitch composite, NOT a
 * literal anatomical brain (which would look amateur in pure SVG).
 *
 * Hand-built. No external libraries beyond Framer Motion. No random noise —
 * fully deterministic so it doesn't flicker between renders.
 */

const VIEWBOX = 480;
const CENTER = VIEWBOX / 2;
const RADIUS = 180;
const NODE_COUNT = 96;

type Node3D = {
  id: number;
  x: number; // -1..1 (sphere)
  y: number;
  z: number;
  active: boolean;
};

/** Fibonacci-spiral sphere — even, deterministic node distribution. */
function buildNodes(): Node3D[] {
  const golden = Math.PI * (3 - Math.sqrt(5));
  const nodes: Node3D[] = [];
  // A handful of deterministic indices that will be the "active" highlight nodes.
  const activeIdx = new Set([6, 19, 37, 52, 68, 81, 90]);

  for (let i = 0; i < NODE_COUNT; i++) {
    const y = 1 - (i / (NODE_COUNT - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const theta = golden * i;
    nodes.push({
      id: i,
      x: Math.cos(theta) * r,
      y,
      z: Math.sin(theta) * r,
      active: activeIdx.has(i),
    });
  }
  return nodes;
}

/** Edges between nearby surface nodes (3D Euclidean threshold). */
function buildEdges(
  nodes: Node3D[],
  threshold: number,
): Array<{ a: number; b: number }> {
  const edges: Array<{ a: number; b: number }> = [];
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const dx = nodes[i].x - nodes[j].x;
      const dy = nodes[i].y - nodes[j].y;
      const dz = nodes[i].z - nodes[j].z;
      const d2 = dx * dx + dy * dy + dz * dz;
      if (d2 < threshold * threshold) edges.push({ a: i, b: j });
    }
  }
  return edges;
}

/** Project a sphere-surface point into 2D with depth (z) influence on size/opacity. */
function project(n: Node3D) {
  // Depth bias toward the viewer (positive z = closer)
  const depth = (n.z + 1) / 2; // 0 (back) .. 1 (front)
  const x = CENTER + n.x * RADIUS;
  const y = CENTER + n.y * RADIUS;
  return { x, y, depth };
}

export function NeuralSphere() {
  const prefersReduced = useReducedMotion();

  const nodes = useMemo(buildNodes, []);
  const edges = useMemo(() => buildEdges(nodes, 0.35), [nodes]);

  const projected = useMemo(() => nodes.map(project), [nodes]);

  return (
    <div
      aria-hidden
      className="relative h-full w-full"
      style={{ aspectRatio: "1 / 1" }}
    >
      {/* Outer halo — soft cream-tinted glow that sits behind the sphere */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(circle at 50% 50%, rgba(94,179,168,0.18) 0%, rgba(13,77,74,0.06) 35%, transparent 65%)",
          filter: "blur(8px)",
        }}
      />

      {/* The sphere itself — Framer rotates the wrapper slowly */}
      <motion.svg
        viewBox={`0 0 ${VIEWBOX} ${VIEWBOX}`}
        width="100%"
        height="100%"
        role="img"
        aria-label="A sphere of interconnected nodes representing a coordination network"
        style={{ overflow: "visible" }}
        animate={prefersReduced ? undefined : { rotate: 360 }}
        transition={
          prefersReduced
            ? undefined
            : { duration: 90, ease: "linear", repeat: Infinity }
        }
      >
        {/* Subtle outer ring — adds weight without crowding the lattice */}
        <circle
          cx={CENTER}
          cy={CENTER}
          r={RADIUS + 6}
          fill="none"
          stroke="#0d4d4a"
          strokeOpacity="0.08"
          strokeWidth="1"
          strokeDasharray="2 6"
        />

        {/* Equatorial reference arc — gives a sense of the sphere's hemisphere */}
        <ellipse
          cx={CENTER}
          cy={CENTER}
          rx={RADIUS}
          ry={RADIUS * 0.18}
          fill="none"
          stroke="#0a0a0f"
          strokeOpacity="0.05"
          strokeWidth="1"
        />

        {/* Edges — depth-aware opacity so back-side connections fade */}
        <g>
          {edges.map(({ a, b }, i) => {
            const pa = projected[a];
            const pb = projected[b];
            const avgDepth = (pa.depth + pb.depth) / 2;
            // Front connections: stronger; back: faint
            const opacity = 0.04 + avgDepth * 0.18;
            const isAccentEdge =
              nodes[a].active || nodes[b].active ? avgDepth > 0.4 : false;
            return (
              <line
                key={`e-${i}`}
                x1={pa.x}
                y1={pa.y}
                x2={pb.x}
                y2={pb.y}
                stroke={isAccentEdge ? "#0d4d4a" : "#0a0a0f"}
                strokeOpacity={isAccentEdge ? 0.45 : opacity}
                strokeWidth={isAccentEdge ? 0.9 : 0.6}
              />
            );
          })}
        </g>

        {/* Nodes — sorted back-to-front so front nodes paint last */}
        <g>
          {projected
            .map((p, i) => ({ p, i, depth: p.depth, active: nodes[i].active }))
            .sort((a, b) => a.depth - b.depth)
            .map(({ p, i, depth, active }) => {
              const baseR = 1.6 + depth * 2.2;
              const opacity = 0.25 + depth * 0.6;

              if (active) {
                return (
                  <g key={`n-${i}`}>
                    {/* Outer accent ring */}
                    <motion.circle
                      cx={p.x}
                      cy={p.y}
                      fill="none"
                      stroke="#0d4d4a"
                      strokeWidth="1.2"
                      animate={
                        prefersReduced
                          ? { r: baseR + 2.5, opacity: 0.45 }
                          : {
                              r: [baseR + 2, baseR + 5.5, baseR + 2],
                              opacity: [0.5, 0.05, 0.5],
                            }
                      }
                      transition={{
                        duration: 3.4,
                        delay: (i % 7) * 0.4,
                        repeat: prefersReduced ? 0 : Infinity,
                        ease: [0.16, 1, 0.3, 1],
                      }}
                    />
                    {/* Solid filled accent dot */}
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={baseR + 0.6}
                      fill="#0d4d4a"
                      fillOpacity={Math.min(1, opacity + 0.2)}
                    />
                  </g>
                );
              }

              return (
                <circle
                  key={`n-${i}`}
                  cx={p.x}
                  cy={p.y}
                  r={baseR}
                  fill="#0a0a0f"
                  fillOpacity={opacity}
                />
              );
            })}
        </g>

        {/* Center anchor — the relay core. Drawn LAST so it sits on top. */}
        <g>
          <circle
            cx={CENTER}
            cy={CENTER}
            r="11"
            fill="#f5f1ea"
            stroke="#0d4d4a"
            strokeWidth="1.4"
          />
          <circle cx={CENTER} cy={CENTER} r="5" fill="#0d4d4a" />
          <circle cx={CENTER} cy={CENTER} r="1.8" fill="#f5f1ea" />
        </g>
      </motion.svg>

      {/* Bottom shadow — anchors the sphere to the surface optically */}
      <div
        aria-hidden
        className="pointer-events-none absolute bottom-[6%] left-1/2 -translate-x-1/2"
        style={{
          width: "62%",
          height: "14px",
          background:
            "radial-gradient(ellipse at center, rgba(10,10,15,0.18) 0%, transparent 70%)",
          filter: "blur(6px)",
        }}
      />
    </div>
  );
}
