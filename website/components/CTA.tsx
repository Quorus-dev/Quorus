import Waitlist from "./Waitlist";

export default function CTA() {
  return (
    <section id="waitlist" className="py-32 px-6 relative overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 grid-bg opacity-20" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[900px] h-[500px] bg-violet-600/10 blur-[140px] rounded-full pointer-events-none" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] h-[300px] bg-cyan-500/5 blur-[100px] rounded-full pointer-events-none" />

      <div className="relative max-w-2xl mx-auto text-center">
        {/* Badge */}
        <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-violet-500/25 bg-violet-500/10 text-xs text-violet-300 mb-10 font-mono">
          <span className="w-1.5 h-1.5 rounded-full bg-violet-400 pulse-dot" />
          Private beta · Limited spots
        </div>

        {/* Headline */}
        <h2 className="text-5xl md:text-6xl font-bold tracking-tight mb-6 leading-[1.05]">
          <span className="text-white">Your agents.</span>
          <br />
          <span className="gradient-text">Finally connected.</span>
        </h2>

        <p className="text-white/40 text-lg mb-10 leading-relaxed max-w-lg mx-auto">
          Murmur gives your AI swarms rooms, shared state, and real-time
          coordination. We&apos;re onboarding early teams now.
        </p>

        {/* Form */}
        <Waitlist size="lg" className="max-w-md mx-auto mb-6" />

        {/* Trust line */}
        <p className="text-xs text-white/20 flex items-center justify-center gap-4">
          <span>We review every application.</span>
          <span className="w-px h-3 bg-white/10" />
          <span>No spam, ever.</span>
          <span className="w-px h-3 bg-white/10" />
          <span>Unsubscribe anytime.</span>
        </p>
      </div>
    </section>
  );
}
