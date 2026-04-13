
export default function AnnouncementBar() {
  return (
    <div className="announcement-glow relative z-[60] flex items-center justify-center h-10 border-b border-amber-500/10 overflow-hidden">
      <div className="shimmer-sweep absolute inset-0 pointer-events-none" />
      <p className="relative text-xs text-white/50 flex items-center gap-2.5">
        <span className="text-amber-400 text-[10px] tracking-widest">
          ✦ PRIVATE BETA
        </span>
        <span className="w-px h-3 bg-white/10" />
        <span>Limited spots now open</span>
        <button
          onClick={() =>
            document
              .getElementById("waitlist")
              ?.scrollIntoView({ behavior: "smooth" })
          }
          className="text-amber-300 hover:text-amber-200 font-medium underline underline-offset-2 transition-colors duration-150"
        >
          Request access →
        </button>
      </p>
    </div>
  );
}
