export default function CTA() {
  return (
    <section className="py-32 px-6 relative overflow-hidden">
      <div className="absolute inset-0 grid-bg opacity-30" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[350px] bg-violet-600/8 blur-[120px] rounded-full pointer-events-none" />

      <div className="relative max-w-3xl mx-auto text-center">
        <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-violet-500/20 bg-violet-500/10 text-xs text-violet-300 mb-8 font-mono">
          <span className="w-1.5 h-1.5 rounded-full bg-violet-400 pulse-dot" />
          Free forever · Open source · Self-hostable
        </div>

        <h2 className="text-5xl md:text-6xl font-bold tracking-tight mb-6 gradient-text">
          Your swarm is waiting
        </h2>
        <p className="text-white/40 text-xl mb-10 leading-relaxed">
          Set up in 3 commands. Your agents coordinate in minutes.
        </p>

        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          <a
            href="https://github.com/Aarya2004/murmur"
            target="_blank"
            rel="noopener noreferrer"
            className="px-8 py-4 rounded-full bg-violet-600 hover:bg-violet-500 text-white font-medium transition-all duration-200 hover:shadow-lg hover:shadow-violet-500/25 text-sm"
          >
            Get Started — it&apos;s free
          </a>
          <a
            href="https://github.com/Aarya2004/murmur"
            target="_blank"
            rel="noopener noreferrer"
            className="px-8 py-4 rounded-full border border-white/10 hover:border-white/20 bg-white/5 hover:bg-white/8 text-white font-medium transition-all duration-200 text-sm"
          >
            View on GitHub →
          </a>
        </div>
      </div>
    </section>
  );
}
