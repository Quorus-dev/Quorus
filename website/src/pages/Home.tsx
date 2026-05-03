import HeroLight from "../components/HeroLight";
import LogoCloud from "../components/LogoCloud";
import ControlCenterDark from "../components/ControlCenterDark";
import BentoLight from "../components/BentoLight";
import CTADark from "../components/CTADark";
import FooterV2 from "../components/FooterV2";

/**
 * Quorus landing — 6-band composition.
 *
 *   1. Hero (cream)            — left copy, right room-state watch panel
 *   2. Logo cloud (cream)      — provider wordmarks
 *   3. Control Center (ink)    — live coordination dashboard mock
 *   4. Bento (cream)           — six primitives, asymmetric grid
 *   5. CTA (ink)               — install command + secondary links
 *   6. Footer (cream)          — sitemap + tagline
 *
 * The Nav inverts itself when scrolled over `data-theme="dark"` sections.
 */
export default function Home() {
  return (
    <main id="main" className="min-h-screen">
      <HeroLight />
      <LogoCloud />
      <ControlCenterDark />
      <BentoLight />
      <CTADark />
      <FooterV2 />
    </main>
  );
}
