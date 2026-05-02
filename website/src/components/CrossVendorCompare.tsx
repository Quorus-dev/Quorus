import { motion } from "framer-motion";
import { Link } from "react-router-dom";

interface Row {
  name: string;
  scope: string;
  works: string;
  isQuorus?: boolean;
}

const ROWS: Row[] = [
  {
    name: "AgentMail",
    scope: "Async mailbox per agent",
    works: "Single vendor",
  },
  {
    name: "Claude Subagents",
    scope: "Sub-Claudes inside one process",
    works: "Anthropic only",
  },
  {
    name: "Google A2A",
    scope: "Protocol spec, no shipped server",
    works: "Spec only",
  },
  {
    name: "Quorus",
    scope: "Rooms + shared state + distributed locks",
    works: "Cross-vendor",
    isQuorus: true,
  },
];

export default function CrossVendorCompare() {
  return (
    <section aria-labelledby="compare-heading" className="relative py-24 px-6">
      <div className="max-w-5xl mx-auto">
        <div className="text-center mb-12">
          <p className="text-xs font-mono text-teal-300 tracking-[0.2em] uppercase mb-4">
            Why Quorus
          </p>
          <h2
            id="compare-heading"
            className="text-4xl md:text-5xl font-bold tracking-[-0.03em] text-white mb-4"
          >
            One layer where every agent meets.
          </h2>
          <p className="text-white/60 text-base md:text-lg max-w-2xl mx-auto">
            Other coordination tools work — inside one vendor. Quorus is the
            only piece that runs across all of them.
          </p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.02] overflow-hidden">
          {/* Header row */}
          <div className="hidden md:grid grid-cols-[1.2fr_2fr_1fr] px-6 py-3 border-b border-white/5 bg-white/[0.02]">
            <span className="text-[10px] font-mono text-white/40 tracking-[0.18em] uppercase">
              Tool
            </span>
            <span className="text-[10px] font-mono text-white/40 tracking-[0.18em] uppercase">
              Scope
            </span>
            <span className="text-[10px] font-mono text-white/40 tracking-[0.18em] uppercase text-right">
              Works
            </span>
          </div>

          {ROWS.map((row, i) => (
            <motion.div
              key={row.name}
              initial={{ opacity: 0, y: 6 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-50px" }}
              transition={{ duration: 0.4, delay: i * 0.06 }}
              className={`grid grid-cols-1 md:grid-cols-[1.2fr_2fr_1fr] gap-1 md:gap-0 px-6 py-5 border-b border-white/5 last:border-b-0 transition-colors ${
                row.isQuorus
                  ? "bg-teal-500/[0.06] hover:bg-teal-500/[0.08]"
                  : "hover:bg-white/[0.02]"
              }`}
            >
              <div className="flex items-center gap-2.5">
                {row.isQuorus && (
                  <span className="w-1.5 h-1.5 rounded-full bg-teal-400 pulse-dot" />
                )}
                <span
                  className={`font-semibold tracking-tight ${
                    row.isQuorus ? "text-teal-300" : "text-white/85"
                  }`}
                >
                  {row.name}
                </span>
              </div>
              <p
                className={`text-sm ${row.isQuorus ? "text-white/85" : "text-white/55"}`}
              >
                {row.scope}
              </p>
              <div className="md:text-right">
                <span
                  className={`inline-block text-[11px] font-mono px-2 py-0.5 rounded-full border ${
                    row.isQuorus
                      ? "border-teal-400/40 bg-teal-500/10 text-teal-300"
                      : "border-white/15 bg-white/[0.02] text-white/45"
                  }`}
                >
                  {row.works}
                </span>
              </div>
            </motion.div>
          ))}
        </div>

        <div className="text-center mt-7">
          <Link
            to="/docs/why-cross-vendor"
            className="inline-flex items-center gap-1.5 text-sm text-teal-300 hover:text-teal-200 transition-colors focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 rounded"
          >
            Read the full breakdown
            <svg
              className="w-3.5 h-3.5"
              viewBox="0 0 20 20"
              fill="currentColor"
              aria-hidden="true"
            >
              <path
                fillRule="evenodd"
                d="M7.3 5.3a1 1 0 011.4 0l4 4a1 1 0 010 1.4l-4 4a1 1 0 01-1.4-1.4L10.6 10 7.3 6.7a1 1 0 010-1.4z"
                clipRule="evenodd"
              />
            </svg>
          </Link>
        </div>
      </div>
    </section>
  );
}
