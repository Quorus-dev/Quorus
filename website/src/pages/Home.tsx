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
import GetStarted from "../components/GetStarted";
import Footer from "../components/Footer";
import VideoDemo from "../components/VideoDemo";

// If/when /public/demo.mp4 exists, set this to "/demo.mp4" (and add a poster
// image at /public/demo-poster.jpg). Until then we render the placeholder.
const DEMO_SRC: string | null = null;
const DEMO_POSTER: string | undefined = undefined;

export default function Home() {
  return (
    <main
      id="main"
      className="min-h-screen"
      style={{ background: "var(--background)" }}
    >
      <Nav />
      <Hero />
      <StatsBand />
      <SocialProof />
      <Features />
      <AgentShowcase />
      <Integrations />
      <LiveSwarmDemo />

      {/* 90-second demo embed */}
      <section
        id="demo"
        aria-labelledby="demo-heading"
        className="relative py-24 px-6"
      >
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-10">
            <p className="text-xs font-mono text-teal-400 tracking-widest uppercase mb-4">
              SEE IT IN ACTION
            </p>
            <h2
              id="demo-heading"
              className="text-4xl md:text-5xl font-bold tracking-tight text-white mb-4"
            >
              Three agents. One file. No conflicts.
            </h2>
            <p className="text-white/60 text-base md:text-lg max-w-2xl mx-auto">
              Watch Claude, Cursor, and Codex coordinate a refactor in real time
              — claim, lock, ship.
            </p>
          </div>
          <VideoDemo src={DEMO_SRC} poster={DEMO_POSTER} />
        </div>
      </section>

      <Architecture />
      <CodeDemo />
      <GetStarted />
      <Footer />
    </main>
  );
}
