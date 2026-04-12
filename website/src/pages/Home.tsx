import Nav from "../components/Nav";
import Hero from "../components/Hero";
import StatsBand from "../components/StatsBand";
import SocialProof from "../components/SocialProof";
import Features from "../components/Features";
import LiveSwarmDemo from "../components/LiveSwarmDemo";
import Architecture from "../components/Architecture";
import CodeDemo from "../components/CodeDemo";
import QuickStart from "../components/QuickStart";
import UseCases from "../components/UseCases";
import CTA from "../components/CTA";
import Footer from "../components/Footer";

export default function Home() {
  return (
    <main className="min-h-screen" style={{ background: "var(--background)" }}>
      <Nav />
      <Hero />
      <StatsBand />
      <SocialProof />
      <Features />
      <LiveSwarmDemo />
      <Architecture />
      <CodeDemo />
      <QuickStart />
      <UseCases />
      <CTA />
      <Footer />
    </main>
  );
}
