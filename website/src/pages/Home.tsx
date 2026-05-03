import HeroLight from "../components/HeroLight";
import LogoCloud from "../components/LogoCloud";
import CrossHarnessBand from "../components/CrossHarnessBand";
import HowItWorksBand from "../components/HowItWorksBand";
import ControlCenterDark from "../components/ControlCenterDark";
import QuickstartBand from "../components/QuickstartBand";
import BentoStitch from "../components/BentoStitch";
import ConsoleTeaserBand from "../components/ConsoleTeaserBand";
import ComparisonBand from "../components/ComparisonBand";
import CTADark from "../components/CTADark";
import FooterV2 from "../components/FooterV2";

/**
 * Quorus landing — 11-band composition.
 *
 *   1. Hero (cream)              — brain + headline + waitlist + install
 *   2. Cross-Harness (ink)       — Claude · Cursor · Gemini · Codex flow
 *   3. Logo cloud (cream)        — provider wordmarks
 *   4. How it works (cream)      — 3-step diagram
 *   5. Control Center (ink)      — live coordination dashboard mock
 *   6. Quickstart (cream)        — tabbed code blocks
 *   7. Bento (ink)               — six interactive primitives
 *   8. Console teaser (cream)    — framed live preview
 *   9. Comparison (cream)        — vs Devin / OpenAgents / TAP / AutoGen
 *  10. CTA (ink)                 — install command + secondary links
 *  11. Footer (cream)            — sitemap + tagline
 *
 * The Nav inverts itself when scrolled over `data-theme="dark"` sections
 * (Cross-Harness, Control Center, Bento, CTA).
 */
export default function Home() {
  return (
    <main id="main" className="min-h-screen">
      <HeroLight />
      <CrossHarnessBand />
      <LogoCloud />
      <HowItWorksBand />
      <ControlCenterDark />
      <QuickstartBand />
      <BentoStitch />
      <ConsoleTeaserBand />
      <ComparisonBand />
      <CTADark />
      <FooterV2 />
    </main>
  );
}
