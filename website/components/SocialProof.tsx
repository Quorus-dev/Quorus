const WORKS_WITH = [
  "Murmur Hub",
  "Claude Code",
  "Cursor",
  "OpenAI Codex",
  "Gemini",
  "Ollama",
  "Any HTTP client",
];

export default function SocialProof() {
  return (
    <section className="py-12 border-y border-white/5 bg-white/[0.01]">
      <div className="max-w-7xl mx-auto px-6">
        <div className="flex flex-col md:flex-row items-center justify-center gap-6 md:gap-10">
          <span className="text-sm text-white/30 shrink-0 font-mono">
            Built for teams using
          </span>
          <div className="flex flex-wrap items-center justify-center gap-4 md:gap-8">
            {WORKS_WITH.map((name) => (
              <span
                key={name}
                className="text-sm text-white/50 hover:text-white/80 transition-colors font-medium"
              >
                {name}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
