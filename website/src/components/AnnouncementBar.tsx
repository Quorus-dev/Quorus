export default function AnnouncementBar() {
  return (
    <div className="relative z-[60] flex items-center justify-center h-9 border-b border-white/[0.06] bg-white/[0.015]">
      <p className="text-[11px] text-white/40 flex items-center gap-2.5 font-mono tracking-wide">
        <span className="w-1 h-1 rounded-full bg-amber-400/70" />
        <span className="text-white/55">PRIVATE BETA</span>
        <span className="text-white/15">·</span>
        <span>Limited spots now open</span>
        <button
          onClick={() =>
            document
              .getElementById("waitlist")
              ?.scrollIntoView({ behavior: "smooth" })
          }
          className="text-amber-400/80 hover:text-amber-300 transition-colors duration-150"
        >
          Request access →
        </button>
      </p>
    </div>
  );
}
