import Nav from "../components/Nav";
import Hero from "../components/Hero";
import StatsBand from "../components/StatsBand";
import SocialProof from "../components/SocialProof";
import Features from "../components/Features";
import AgentShowcase from "../components/AgentShowcase";
import Integrations from "../components/Integrations";
import LiveSwarmDemo from "../components/LiveSwarmDemo";
import Architecture from "../components/Architecture";
import CodeDemo from "../components/CodeDemo";
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
      <AgentShowcase />
      <Integrations />
      <LiveSwarmDemo />
      <Architecture />
      <CodeDemo />
      <CTA />
      <Footer />
    </main>
  );
}
