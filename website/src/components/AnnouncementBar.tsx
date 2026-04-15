export default function AnnouncementBar() {
  return (
    <div className="relative z-[60] flex items-center justify-center h-9 border-b border-white/[0.06] bg-white/[0.015]">
      <p className="text-[11px] text-white/70 flex items-center gap-2.5 font-mono tracking-wide">
        <span className="w-1 h-1 rounded-full bg-teal-400/70" />
        <span className="text-white/80">OPEN BETA</span>
        <span className="text-white/35">·</span>
        <span>v0.4</span>
        <span className="text-white/35">·</span>
        <span>MIT licensed</span>
        <a
          href="https://github.com/Quorus-dev/Quorus"
          target="_blank"
          rel="noopener noreferrer"
          className="text-teal-300 hover:text-teal-200 transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 rounded"
        >
          View on GitHub →
        </a>
      </p>
    </div>
  );
}
