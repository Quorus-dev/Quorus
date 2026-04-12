"use client";
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
  { value: 3.6, suffix: "ms", decimals: 1, label: "p50 latency" },
  { value: 281, suffix: " msg/s", label: "throughput" },
  { value: 866, suffix: "+", label: "tests passing" },
  { value: 99.9, suffix: "%", decimals: 1, label: "uptime" },
];

export default function StatsBand() {
  return (
    <section className="relative py-14 overflow-hidden border-y border-white/[0.05]">
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 60% 100% at 50% 50%, rgba(124,58,237,0.03) 0%, transparent 70%)",
        }}
      />

      <div className="max-w-5xl mx-auto px-6">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-10 md:gap-0 md:divide-x md:divide-white/[0.05]">
          {STATS.map((stat, i) => (
            <motion.div
              key={stat.label}
              initial={{ opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.1, duration: 0.5, ease: "easeOut" }}
              className="flex flex-col items-center text-center gap-2"
            >
              <div className="text-3xl md:text-4xl font-bold tabular-nums tracking-tight text-white">
                <Counter
                  value={stat.value}
                  suffix={stat.suffix}
                  decimals={stat.decimals}
                />
              </div>
              <div className="text-xs text-white/30 font-mono tracking-widest uppercase">
                {stat.label}
              </div>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
