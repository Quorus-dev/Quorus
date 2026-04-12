export default function Footer() {
  return (
    <footer className="border-t border-white/5 py-12 px-6">
      <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-6">
        {/* Logo */}
        <div className="flex items-center gap-2.5">
          <div className="w-2 h-2 rounded-full bg-violet-500" />
          <span className="font-mono text-base font-semibold text-white/70">
            murmur
          </span>
        </div>

        {/* Links */}
        <div className="flex items-center gap-6 text-sm text-white/25">
          <a href="#features" className="hover:text-white/60 transition-colors">
            Features
          </a>
          <a href="#howit" className="hover:text-white/60 transition-colors">
            How it works
          </a>
          <a href="#waitlist" className="hover:text-white/60 transition-colors">
            Join waitlist
          </a>
        </div>

        {/* Built by + copyright */}
        <p className="text-xs text-white/15 font-mono">
          © 2026 Murmur · Built by Arav &amp; Aarya
        </p>
      </div>
    </footer>
  );
}
