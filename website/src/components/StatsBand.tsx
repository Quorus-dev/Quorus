import { useRef, useEffect, useState } from "react";
import { useInView, motion } from "framer-motion";

function Counter({
  value,
  suffix = "",
  decimals = 0,
}: {
  value: number;
  suffix?: string;
  decimals?: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (!inView) return;
    const duration = 1600;
    const start = Date.now();
    const tick = () => {
      const elapsed = Date.now() - start;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(eased * value);
      if (progress < 1) requestAnimationFrame(tick);
      else setDisplay(value);
    };
    requestAnimationFrame(tick);
  }, [inView, value]);

  const formatted =
    decimals > 0 ? display.toFixed(decimals) : Math.floor(display).toString();
  return (
    <span ref={ref}>
      {formatted}
      {suffix}
    </span>
  );
}

const STATS = [
  { value: 11, suffix: "", decimals: 0, label: "MCP tools" },
  { value: 870, suffix: "+", label: "tests passing" },
  { value: 100, suffix: "%", decimals: 0, label: "open source" },
  { value: 0, suffix: "", decimals: 0, label: "YAML required" },
];

export default function StatsBand() {
  return (
    <section className="relative py-20 overflow-hidden border-y border-white/[0.05]">
      {/* Ambient glow */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 80% 60% at 50% 50%, rgba(20,184,166,0.06), transparent)",
        }}
      />

      <div className="max-w-5xl mx-auto px-6">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-10 md:gap-0 md:divide-x md:divide-white/[0.05]">
          {STATS.map((stat, i) => (
            <motion.div
              key={stat.label}
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.1, duration: 0.55, ease: "easeOut" }}
              className="flex flex-col items-center text-center gap-3"
            >
              {/* Big amber-gradient number */}
              <div
                className="text-5xl md:text-6xl font-bold tabular-nums tracking-tight"
                style={{
                  background:
                    "linear-gradient(135deg, #ccfbf1 0%, #2dd4bf 45%, #14b8a6 100%)",
                  WebkitBackgroundClip: "text",
                  WebkitTextFillColor: "transparent",
                  backgroundClip: "text",
                }}
              >
                <Counter
                  value={stat.value}
                  suffix={stat.suffix}
                  decimals={stat.decimals}
                />
              </div>
              <div className="text-[10px] text-white/30 font-mono tracking-widest uppercase">
                {stat.label}
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
