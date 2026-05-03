import { useMemo } from "react";
import { useReducedMotion } from "framer-motion";

/**
 * BrainHero — hand-crafted SVG illustration of a side-profile glass brain
 * with internal teal synapses pulsing on a 3.4s loop. Replaces the raster
 * brain-scene.webp asset so the hero scales crisply on any DPR.
 *
 * Anatomy (viewBox 0 0 776 700, brain faces RIGHT):
 *   - Frontal lobe ........ x=[500,720]  y=[150,380]   (right side of view)
 *   - Crown / parietal .... x=[280,600]  y=[80, 220]
 *   - Temporal lobe ....... x=[200,480]  y=[280,480]
 *   - Occipital ........... x=[80, 250]  y=[230,420]   (left side of view)
 *   - Cerebellum .......... x=[150,320]  y=[420,580]
 *   - Brainstem ........... x=[260,320]  y=[540,670]
 *
 * Composition layers (back -> front):
 *   1. Soft halo behind the silhouette (atmosphere)
 *   2. Outer silhouette path with glass gradient fill + faint stroke
 *   3. Cortical gyri/sulci — organic Bezier strokes inside the silhouette
 *   4. Cerebellum sub-shape with diagonal striation hatching
 *   5. Brainstem stub
 *   6. 14 synapse nodes at anatomical hotspots — teal dot + pulsing halo
 *   7. Edges between nearby nodes (dashed, shimmering with stroke-dashoffset)
 *   8. Glass highlight reflections near top-front
 *
 * Honors `prefers-reduced-motion`: no pulses, no shimmer, end-state render.
 */

type Node = { x: number; y: number };

// 14 active synapse nodes, anatomically distributed across the lobes. Coords
// in the same 776×700 viewBox so they composite correctly with the paths.
// Brain faces RIGHT: frontal=high x, occipital=low x, cerebellum=lower-left.
const NODES: readonly Node[] = [
  // Frontal lobe (3) — right side of brain
  { x: 625, y: 220 },
  { x: 665, y: 290 },
  { x: 605, y: 360 },
  // Parietal / crown (3) — top
  { x: 480, y: 115 },
  { x: 380, y: 125 },
  { x: 560, y: 185 },
  // Temporal lobe (3) — middle/lower (the bright "hub" sits here)
  { x: 540, y: 460 },
  { x: 470, y: 485 },
  { x: 405, y: 470 },
  // Occipital (2) — back-left
  { x: 195, y: 280 },
  { x: 155, y: 360 },
  // Cerebellum (2) — lower-back
  { x: 230, y: 510 },
  { x: 290, y: 540 },
  // Brainstem (1)
  { x: 332, y: 605 },
] as const;

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

// Outer silhouette — a side-profile brain facing RIGHT. Anatomically accurate
// landmarks (clockwise from top-back of parietal):
//
//   crown sweep .................... top arc
//   frontal lobe bulge ............. forward-most rounded mass on the right
//   orbital surface ................ underside of frontal, dips up briefly
//   lateral fissure inward dent .... small concavity (Sylvian fissure mouth)
//   temporal pole .................. forward-bottom thumb-like protrusion
//   temporal underside ............. sweeps back along the bottom
//   cerebellar overlap ............. small inward dip where cerebellum joins
//   occipital pole ................. rear-most rounded bulge on the left
//   back-of-head sweep ............. up the back
//
// Cerebellum and brainstem are separate paths drawn afterward so they
// integrate with the silhouette underside without seam artifacts.
const SILHOUETTE =
  // Start at top-back of parietal, near the crown
  "M 220 110 " +
  // Crown — sweep up and across the top, ending at top of frontal lobe
  "C 290 70, 410 62, 545 78 " +
  // Up over the frontal-lobe bulge then forward-down to the front of the head
  "C 625 92, 685 145, 705 235 " +
  // Frontal forehead bulges further forward then curves down to the orbital region
  "C 712 295, 690 350, 640 388 " +
  // Orbital surface — gentle continuous curve down to the temporal pole
  // (no inward notch — the Sylvian fissure is drawn as an INTERIOR stroke)
  "C 615 408, 595 415, 585 432 " +
  // Temporal pole — distinct forward-down protrusion (the "thumb")
  "C 590 460, 580 490, 545 510 " +
  // Underside of temporal lobe — sweeps back along the bottom
  "C 510 525, 460 528, 405 518 " +
  // Bridge into the cerebellar/pons region (slight inward concavity)
  "C 380 513, 360 508, 345 502 " +
  // Cerebellum-occipital junction — smooth transition into back-bottom
  "C 305 495, 250 482, 195 455 " +
  // Back-bottom of occipital
  "C 145 430, 105 395, 90 345 " +
  // Occipital pole — rounded back of head
  "C 78 300, 80 250, 100 205 " +
  // Up the back of the parietal, curving forward over the top
  "C 120 170, 150 142, 180 126 " +
  // Close to crown start
  "C 195 119, 210 113, 220 110 Z";

// Cortical fold strokes — gyri/sulci hints. The Sylvian (lateral) fissure
// is the dominant horizontal feature in any side-view brain, so it gets the
// strongest stroke. Other folds suggest the central sulcus, parieto-occipital
// fissure, and a few minor gyral arcs across each lobe.
const GYRI: readonly { d: string; w: number; o: number }[] = [
  // Lateral (Sylvian) fissure — THE defining horizontal groove. Sweeps from
  // the temporal pole back/up through the brain, separating temporal from
  // frontal+parietal. Drawn boldest.
  { d: "M 600 380 C 510 380, 410 385, 320 400", w: 1.6, o: 0.42 },
  // Central sulcus — diagonal across the crown, slightly behind the midline
  { d: "M 430 95 C 440 165, 430 230, 405 295", w: 1.3, o: 0.32 },
  // Parieto-occipital sulcus — short diagonal at the back-top
  { d: "M 235 130 C 220 175, 215 220, 230 270", w: 1.1, o: 0.28 },
  // Frontal gyrus — arc within the frontal lobe
  { d: "M 580 110 C 625 155, 660 215, 660 285", w: 1.0, o: 0.25 },
  // Pre-frontal upper fold
  { d: "M 510 90 C 545 135, 575 190, 580 250", w: 0.9, o: 0.22 },
  // Superior frontal gyrus arc
  { d: "M 470 80 C 490 130, 510 180, 515 240", w: 0.8, o: 0.2 },
  // Parietal upper fold (behind the central sulcus)
  { d: "M 320 90 C 335 145, 355 200, 370 260", w: 0.95, o: 0.24 },
  // Inferior parietal fold near the Sylvian
  { d: "M 290 230 C 315 270, 345 305, 380 330", w: 0.8, o: 0.22 },
  // Occipital fold near the back
  { d: "M 145 200 C 120 250, 110 305, 130 360", w: 1.0, o: 0.26 },
  // Temporal lobe gyrus — runs along the upper temporal
  { d: "M 580 415 C 510 420, 440 425, 380 430", w: 0.9, o: 0.24 },
  // Lower temporal gyrus
  { d: "M 555 460 C 490 470, 425 478, 380 475", w: 0.85, o: 0.22 },
  // Short frontal pole curl
  { d: "M 615 250 C 645 275, 660 305, 650 340", w: 0.8, o: 0.2 },
];

// Cerebellum — rounded "cauliflower" lobe tucked under the occipital region
// at the back-bottom of the brain. Sits PARTIALLY INSIDE the silhouette
// (top half hidden behind it) and PROTRUDES DOWN-BACK so it reads as
// anatomically attached. Brainstem joins on the right side.
const CEREBELLUM =
  // Top-left — start INSIDE the silhouette so seam is hidden
  "M 155 395 " +
  // Top — sweeps right under the silhouette underside
  "C 200 425, 260 450, 320 470 " +
  // Top-right — extends up-right toward brainstem junction
  "C 350 485, 365 500, 365 525 " +
  // Right edge — curves down to brainstem
  "C 360 555, 335 580, 305 590 " +
  // Bottom-right
  "C 270 600, 235 600, 205 593 " +
  // Bottom — round cerebellar belly
  "C 170 585, 135 565, 122 535 " +
  // Bottom-left — soft curve back up
  "C 110 510, 115 475, 135 445 " +
  // Left edge back up
  "C 142 430, 148 415, 155 395 Z";

// Cerebellar folia — fine parallel arcs suggesting the textured surface
// of the cerebellum. Drawn as horizontal-ish curves following the bulk.
const CEREBELLUM_HATCH: readonly { d: string }[] = [
  { d: "M 145 460 C 205 480, 270 495, 325 500" },
  { d: "M 135 485 C 195 505, 270 515, 345 520" },
  { d: "M 128 510 C 190 528, 270 538, 350 538" },
  { d: "M 128 535 C 190 552, 270 562, 345 558" },
  { d: "M 140 560 C 195 575, 265 583, 325 580" },
  { d: "M 165 583 C 210 593, 265 595, 305 588" },
];

// Brainstem — connects the cerebellum to the spinal cord. Slightly tapered
// cylinder dropping down/forward from the cerebellum-temporal junction.
const BRAINSTEM =
  "M 350 520 " +
  // Right side down, narrowing slightly forward (toward spinal cord)
  "C 362 565, 365 610, 350 650 " +
  // Bottom — rounded base
  "C 340 665, 320 665, 310 650 " +
  // Left side back up to cerebellum
  "C 305 610, 315 565, 328 520 Z";

// Glass highlight reflections — short curved strokes near the top-front
// suggesting gloss on a transparent surface.
const HIGHLIGHTS: readonly { d: string; o: number; w: number }[] = [
  // Crown highlight — soft top-of-glass gloss
  { d: "M 310 80 C 380 70, 460 70, 530 82", o: 0.4, w: 1.0 },
  // Frontal-pole highlight — front of forehead
  { d: "M 645 165 C 675 205, 695 245, 698 290", o: 0.32, w: 1.0 },
  // Short crown reflection — second pass, faint
  { d: "M 360 95 C 410 88, 460 88, 500 95", o: 0.3, w: 0.7 },
];

const PULSE_DURATION_S = 3.4;
const PULSE_STAGGER_S = 0.4;
const EDGE_SHIMMER_DURATION_S = 8;
const DOT_RADIUS = 4;
const TEAL = "#5eb3a8"; // matches var(--color-accent-on-ink) palette
const INK = "#0a0a0f";

export default function BrainHero(): JSX.Element {
  const prefersReduced = useReducedMotion();
  const edges = useMemo(() => computeEdges(NODES, EDGE_DISTANCE_THRESHOLD), []);

  return (
    <svg
      role="img"
      aria-label="A glass brain with bright teal synapses representing the Quorus coordination network"
      className="relative block h-auto w-full select-none"
      viewBox="0 0 776 700"
      preserveAspectRatio="xMidYMid meet"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        {/* Glass gradient for the silhouette fill — soft top-light, cool bottom */}
        <linearGradient id="brainGlass" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="0.55" />
          <stop offset="55%" stopColor="#e9f3f0" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#cfe7e2" stopOpacity="0.45" />
        </linearGradient>

        {/* Inner radial — adds depth as if light is coming from inside */}
        <radialGradient id="brainInner" cx="0.55" cy="0.45" r="0.65">
          <stop offset="0%" stopColor={TEAL} stopOpacity="0.18" />
          <stop offset="60%" stopColor={TEAL} stopOpacity="0.04" />
          <stop offset="100%" stopColor={TEAL} stopOpacity="0" />
        </radialGradient>

        {/* Soft halo behind the brain */}
        <radialGradient id="brainHalo" cx="0.5" cy="0.5" r="0.55">
          <stop offset="0%" stopColor={TEAL} stopOpacity="0.22" />
          <stop offset="55%" stopColor={TEAL} stopOpacity="0.06" />
          <stop offset="100%" stopColor={TEAL} stopOpacity="0" />
        </radialGradient>

        {/* Glow filter applied to synapse dots */}
        <filter id="brainGlow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="3.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>

        {/* Stronger glow for the brightest "hub" nodes */}
        <filter
          id="brainGlowStrong"
          x="-80%"
          y="-80%"
          width="260%"
          height="260%"
        >
          <feGaussianBlur stdDeviation="6" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>

        {/* Clip cortical detail to inside the silhouette so curves never bleed */}
        <clipPath id="brainClip">
          <path d={SILHOUETTE} />
        </clipPath>

        {/* Clip cerebellar folia to inside the cerebellum lobe */}
        <clipPath id="cerebellumClip">
          <path d={CEREBELLUM} />
        </clipPath>
      </defs>

      {/* 1. Background halo */}
      <ellipse cx="400" cy="340" rx="340" ry="280" fill="url(#brainHalo)" />

      {/* 2. Brainstem — drawn FIRST so the cerebellum + silhouette cover its
          top edge, leaving only the protruding bottom visible */}
      <path
        d={BRAINSTEM}
        fill="url(#brainGlass)"
        stroke={INK}
        strokeOpacity="0.25"
        strokeWidth="1.2"
        strokeLinejoin="round"
      />

      {/* 3. Cerebellum body — drawn BEFORE silhouette so the silhouette's
          bottom edge becomes the visible boundary, hiding the seam */}
      <path
        d={CEREBELLUM}
        fill="url(#brainGlass)"
        stroke={INK}
        strokeOpacity="0.32"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
      {/* Cerebellum folia — clipped to cerebellum lobe */}
      <g clipPath="url(#cerebellumClip)">
        {CEREBELLUM_HATCH.map((h, i) => (
          <path
            key={`hatch-${i}`}
            d={h.d}
            stroke={INK}
            strokeOpacity="0.3"
            strokeWidth="0.7"
            strokeLinecap="round"
            fill="none"
          />
        ))}
      </g>

      {/* 4. Outer silhouette — the brain shape itself, drawn over cerebellum
          so the seam between the two is hidden */}
      <path
        d={SILHOUETTE}
        fill="url(#brainGlass)"
        stroke={INK}
        strokeOpacity="0.28"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      {/* Inner radial overlay — adds subtle inside-glow */}
      <path d={SILHOUETTE} fill="url(#brainInner)" />

      {/* 5. Cortical gyri/sulci — clipped to silhouette */}
      <g clipPath="url(#brainClip)">
        {GYRI.map((g, i) => (
          <path
            key={`gyrus-${i}`}
            d={g.d}
            stroke={INK}
            strokeOpacity={g.o}
            strokeWidth={g.w}
            strokeLinecap="round"
            fill="none"
          />
        ))}
      </g>

      {/* 7. Edges between nearby synapses — drawn under the dots */}
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
              stroke={TEAL}
              strokeOpacity={prefersReduced ? 0.4 : 0.3}
              strokeWidth={0.8}
              strokeDasharray="3 5"
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

      {/* 6. Synapse nodes — pulse rings + solid dots with glow filter */}
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
                stroke={TEAL}
                strokeWidth={1}
                opacity={0}
              >
                <animate
                  attributeName="r"
                  values="5;16"
                  dur={`${PULSE_DURATION_S}s`}
                  begin={begin}
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="opacity"
                  values="0.7;0"
                  dur={`${PULSE_DURATION_S}s`}
                  begin={begin}
                  repeatCount="indefinite"
                />
              </circle>
            );
          })}
        </g>
      )}

      <g>
        {NODES.map((node, i) => {
          // Hub nodes (the brightest sphere-like spots in the reference) get
          // the strong glow filter. Everything else gets the light glow.
          const isHub = i === 6 || i === 0 || i === 11;
          return (
            <circle
              key={`dot-${i}`}
              cx={node.x}
              cy={node.y}
              r={isHub ? DOT_RADIUS + 1 : DOT_RADIUS}
              fill={TEAL}
              opacity={prefersReduced ? 1 : 0.95}
              filter={`url(#${isHub ? "brainGlowStrong" : "brainGlow"})`}
            />
          );
        })}
      </g>

      {/* 8. Glass highlight reflections — top-front gloss */}
      <g>
        {HIGHLIGHTS.map((h, i) => (
          <path
            key={`hl-${i}`}
            d={h.d}
            stroke="#ffffff"
            strokeOpacity={h.o}
            strokeWidth={h.w}
            strokeLinecap="round"
            fill="none"
          />
        ))}
      </g>
    </svg>
  );
}
