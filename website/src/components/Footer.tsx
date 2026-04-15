export default function Footer() {
  return (
    <footer className="border-t border-white/5 py-12 px-6">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
        {/* Logo */}
        <div className="flex items-center gap-2.5">
          <div className="w-2 h-2 rounded-full bg-teal-500" />
          <span className="font-mono text-base font-semibold text-white/70">
            quorus
          </span>
        </div>

        {/* Links */}
        <div className="flex items-center gap-6 text-sm text-white/55">
          <a
            href="#features"
            className="hover:text-white transition-colors focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 rounded"
          >
            Features
          </a>
          <a
            href="#architecture"
            className="hover:text-white transition-colors focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 rounded"
          >
            Architecture
          </a>
          <a
            href="#waitlist"
            className="hover:text-white transition-colors focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 rounded"
          >
            Join waitlist
          </a>
        </div>

        {/* Built by + copyright */}
        <p className="text-xs text-white/40 font-mono">
          © 2026 Quorus · Built by Arav &amp; Aarya
        </p>
      </div>
    </footer>
  );
}
