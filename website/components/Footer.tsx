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
        <div className="flex items-center gap-6 text-sm text-white/30">
          <a
            href="https://github.com/Aarya2004/murmur"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-white/70 transition-colors"
          >
            GitHub
          </a>
          <a
            href="https://github.com/Aarya2004/murmur#reference"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-white/70 transition-colors"
          >
            Docs
          </a>
          <a
            href="https://github.com/Aarya2004/murmur/blob/main/LICENSE"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-white/70 transition-colors"
          >
            MIT License
          </a>
        </div>

        {/* Built by */}
        <p className="text-sm text-white/20 font-mono">
          Built by Arav &amp; Aarya
        </p>
      </div>
    </footer>
  );
}
