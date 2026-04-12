import Nav from "@/components/Nav";
import Hero from "@/components/Hero";
import SocialProof from "@/components/SocialProof";
import Features from "@/components/Features";
import Architecture from "@/components/Architecture";
import CodeDemo from "@/components/CodeDemo";
import QuickStart from "@/components/QuickStart";
import UseCases from "@/components/UseCases";
import ManagedService from "@/components/ManagedService";
import CTA from "@/components/CTA";
import Footer from "@/components/Footer";

export default function Home() {
  return (
    <main className="min-h-screen bg-black">
      <Nav />
      <Hero />
      <SocialProof />
      <Features />
      <Architecture />
      <CodeDemo />
      <QuickStart />
      <UseCases />
      <ManagedService />
      <CTA />
      <Footer />
    </main>
  );
}
