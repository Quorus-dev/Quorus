import HeroLight from "../components/HeroLight";
import OsPrimitivesTable from "../components/OsPrimitivesTable";
import CrossHarnessBand from "../components/CrossHarnessBand";
import ComparisonBand from "../components/ComparisonBand";
import BentoStitch from "../components/BentoStitch";
import CTADark from "../components/CTADark";
import FooterV2 from "../components/FooterV2";

/**
 * Quorus landing — 6-section composition.
 *
 *   1. Hero (cream)         — agent-native OS framing, install, spec CTA
 *   2. OS Primitives (ink)  — eight-row table; LIVE today + roadmap
 *   3. Cross-Harness (ink)  — deep dive on the LIVE Coordination primitive
 *   4. Comparison (cream)   — Quorus vs Devin/OpenAgents/TAP/AutoGen capability matrix
 *   5. Bento (ink)          — four product surfaces (Rooms, State, MCP, Sync)
 *   6. CTA (ink)            — convert
 *      + Footer (cream)     — sitemap + tagline
 *
 * The OS Primitives band is the bridge between the Plan v8 hero positioning
 * ("the agent-native operating system") and the existing cross-harness band,
 * which now reads as proof-of-life on the first LIVE primitive (Coordination).
 * The Nav inverts itself when scrolled over `data-theme="dark"` sections
 * (OS Primitives, Cross-Harness, Bento, CTA).
 */
export default function Home() {
  return (
    <main id="main" className="min-h-screen">
      <HeroLight />
      <OsPrimitivesTable />
      <CrossHarnessBand />
      <ComparisonBand />
      <BentoStitch />
      <CTADark />
      <FooterV2 />
    </main>
  );
}
