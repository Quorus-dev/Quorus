import HeroLight from "../components/HeroLight";
import CrossHarnessBand from "../components/CrossHarnessBand";
import BentoStitch from "../components/BentoStitch";
import CTADark from "../components/CTADark";
import FooterV2 from "../components/FooterV2";

/**
 * Quorus landing — 4-section composition (down from 11).
 *
 *   1. Hero (cream)         — full-viewport, headline, live room, install, scroll cue
 *   2. Cross-Harness (ink)  — Claude · Cursor · Gemini · Codex flow + tabs + marquee
 *   3. Bento (ink)          — four primitives (Rooms, State, MCP, Context Sync)
 *   4. CTA (ink)            — convert
 *      + Footer (cream)     — sitemap + tagline
 *
 * Cut from prior 11-section build: LogoCloud (folded into CrossHarness),
 * HowItWorksBand, ControlCenterDark, QuickstartBand, ConsoleTeaserBand,
 * ComparisonBand. The Nav inverts itself when scrolled over `data-theme="dark"`
 * sections (Cross-Harness, Bento, CTA).
 */
export default function Home() {
  return (
    <main id="main" className="min-h-screen">
      <HeroLight />
      <CrossHarnessBand />
      <BentoStitch />
      <CTADark />
      <FooterV2 />
    </main>
  );
}
