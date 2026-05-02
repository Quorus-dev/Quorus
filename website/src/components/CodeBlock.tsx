import { useCallback, useId, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface CodeBlockProps {
  command: string;
  label?: string;
  prompt?: string;
  /** Show line number gutter (default true). */
  showLineNumber?: boolean;
}

export default function CodeBlock({
  command,
  label,
  prompt = "$",
  showLineNumber = true,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const labelId = useId();

  const onCopy = useCallback(() => {
    // Modern browsers — Clipboard API. Fallback for non-HTTPS dev envs.
    const succeed = () => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    };
    if (
      typeof navigator !== "undefined" &&
      navigator.clipboard &&
      window.isSecureContext
    ) {
      navigator.clipboard
        .writeText(command)
        .then(succeed)
        .catch(() => {});
    } else {
      // Fallback: hidden textarea
      const ta = document.createElement("textarea");
      ta.value = command;
      ta.setAttribute("readonly", "");
      ta.style.position = "absolute";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        succeed();
      } catch {
        // ignore
      }
      document.body.removeChild(ta);
    }
  }, [command]);

  return (
    <div className="w-full">
      {label ? (
        <p
          id={labelId}
          className="text-[11px] font-mono text-white/50 mb-2 tracking-wide uppercase"
        >
          {label}
        </p>
      ) : null}
      <div
        className="group relative flex items-center gap-3 px-4 py-3 rounded-xl border border-white/10 bg-[#0a0a14]/80 backdrop-blur-sm font-mono text-xs sm:text-sm overflow-x-auto shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] hover:border-teal-500/30 transition-colors"
        aria-labelledby={label ? labelId : undefined}
      >
        {showLineNumber && (
          <span
            aria-hidden="true"
            className="select-none text-white/25 tabular-nums pr-2 border-r border-white/[0.06] mr-1"
          >
            1
          </span>
        )}
        <span className="text-teal-400 shrink-0" aria-hidden="true">
          {prompt}
        </span>
        <code className="text-white/90 whitespace-nowrap">{command}</code>
        {/* Rotating block cursor — sits at the end of the install line */}
        <span
          aria-hidden="true"
          className="inline-block w-1.5 h-3.5 -mb-0.5 bg-teal-400 ml-px shrink-0 animate-pulse"
        />
        <button
          type="button"
          aria-label={copied ? "Copied to clipboard" : "Copy command"}
          onClick={onCopy}
          className="ml-auto shrink-0 inline-flex items-center justify-center w-9 h-9 rounded-md text-white/55 hover:text-teal-300 hover:bg-white/[0.06] focus-visible:text-teal-300 focus-visible:bg-white/[0.06] focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 transition-colors"
        >
          <AnimatePresence mode="wait" initial={false}>
            {copied ? (
              <motion.svg
                key="check"
                initial={{ scale: 0.6, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.6, opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="w-4 h-4 text-teal-300"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2.2}
                  d="M5 13l4 4L19 7"
                />
              </motion.svg>
            ) : (
              <motion.svg
                key="copy"
                initial={{ scale: 0.6, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.6, opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="w-4 h-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                />
              </motion.svg>
            )}
          </AnimatePresence>
        </button>
        {/* Floating "Copied" toast — non-intrusive, fades in/out */}
        <AnimatePresence>
          {copied && (
            <motion.span
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.18 }}
              className="absolute -top-7 right-2 px-2 py-0.5 rounded-md bg-teal-500 text-black text-[10px] font-mono font-semibold tracking-widest uppercase pointer-events-none shadow-lg"
            >
              Copied
            </motion.span>
          )}
        </AnimatePresence>
      </div>
      {/* Polite, screen-reader-only live region for copy confirmation */}
      <div role="status" aria-live="polite" className="sr-only">
        {copied ? "Copied to clipboard" : ""}
      </div>
    </div>
  );
}
