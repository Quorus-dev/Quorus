import { useCallback, useId, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

/**
 * CodeBlock — dark-surface code presenter for the cream/ink design system.
 *
 * No syntax highlighting library. Just monospace text with an optional
 * accent-colored prompt prefix (e.g. "$ ") and a subtle copy button that
 * flashes a checkmark for ~1.8s after a successful clipboard write.
 *
 * Variants:
 *   - bare command:     just `command`
 *   - language tag:     `lang="python"` renders a small mono label, top-right
 *   - filename header:  `filename="server.py"` renders a tab-style chrome above
 *                       the code with mac-style traffic-light dots
 *
 * Designed to be content-agnostic and forward-compatible: pages pass either a
 * single shell command OR a multi-line code body. Multi-line bodies render
 * vertically with horizontal scroll on overflow (never breaking layout).
 */
interface CodeBlockProps {
  /** The code body. Single line or multiline. */
  command: string;
  /** Caption shown ABOVE the block. Mono, uppercase, muted. */
  label?: string;
  /** Prompt prefix rendered in accent color. Pass empty string to hide. */
  prompt?: string;
  /** Language tag rendered in the top-right corner of the body. */
  lang?: string;
  /** Filename — when present, renders a tab-style header strip. */
  filename?: string;
}

const EASE = [0.16, 1, 0.3, 1] as const;

export default function CodeBlock({
  command,
  label,
  prompt = "$",
  lang,
  filename,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const labelId = useId();

  const onCopy = useCallback(() => {
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
      return;
    }
    // Fallback for non-secure dev contexts
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
  }, [command]);

  const isMultiline = command.includes("\n");
  const lines = isMultiline ? command.split("\n") : [command];

  return (
    <div className="w-full my-5">
      {label ? (
        <p
          id={labelId}
          className="mb-2 font-mono text-[11px] uppercase tracking-[0.18em]"
          style={{ color: "var(--color-text-on-cream-muted)" }}
        >
          {label}
        </p>
      ) : null}

      <div
        className="overflow-hidden"
        style={{
          backgroundColor: "var(--color-ink-2)",
          border: "1px solid var(--color-border-dark)",
          borderRadius: "var(--radius-md)",
        }}
        aria-labelledby={label ? labelId : undefined}
      >
        {filename ? (
          <div
            className="flex items-center gap-3 px-4 py-2.5"
            style={{
              borderBottom: "1px solid var(--color-border-dark)",
              backgroundColor: "rgba(255,255,255,0.02)",
            }}
          >
            <div className="flex gap-1.5">
              <span
                className="block h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: "rgba(255,255,255,0.10)" }}
              />
              <span
                className="block h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: "rgba(255,255,255,0.10)" }}
              />
              <span
                className="block h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: "rgba(255,255,255,0.10)" }}
              />
            </div>
            <span
              className="ml-1 font-mono text-[11px]"
              style={{ color: "var(--color-text-on-ink-muted)" }}
            >
              {filename}
            </span>
            {lang ? (
              <span
                className="ml-auto font-mono text-[10.5px] uppercase tracking-[0.14em]"
                style={{ color: "var(--color-text-on-ink-muted)" }}
              >
                {lang}
              </span>
            ) : null}
          </div>
        ) : null}

        <div className="group relative">
          {!filename && lang ? (
            <span
              className="pointer-events-none absolute right-14 top-3 font-mono text-[10.5px] uppercase tracking-[0.14em]"
              style={{ color: "var(--color-text-on-ink-muted)" }}
            >
              {lang}
            </span>
          ) : null}

          <pre
            className="overflow-x-auto px-4 py-3.5 font-mono text-[12.5px] leading-[1.6] sm:text-[13px]"
            style={{ color: "var(--color-text-on-ink)", margin: 0 }}
          >
            <code className="block whitespace-pre">
              {lines.map((line, i) => (
                <span key={i} className="block">
                  {prompt && !isMultiline ? (
                    <>
                      <span
                        aria-hidden="true"
                        style={{ color: "var(--color-accent-on-ink)" }}
                      >
                        {prompt}
                      </span>
                      <span aria-hidden="true"> </span>
                    </>
                  ) : null}
                  {line || " "}
                </span>
              ))}
            </code>
          </pre>

          <button
            type="button"
            aria-label={copied ? "Copied to clipboard" : "Copy code"}
            onClick={onCopy}
            className="absolute right-2 top-2 inline-flex h-9 w-9 items-center justify-center rounded-md transition-colors focus-visible:outline-2 focus-visible:outline-offset-2"
            style={{
              color: copied
                ? "var(--color-accent-on-ink)"
                : "var(--color-text-on-ink-muted)",
              backgroundColor: copied ? "rgba(94,179,168,0.10)" : "transparent",
            }}
            onMouseEnter={(e) => {
              if (!copied) {
                e.currentTarget.style.backgroundColor =
                  "rgba(255,255,255,0.06)";
                e.currentTarget.style.color = "var(--color-text-on-ink)";
              }
            }}
            onMouseLeave={(e) => {
              if (!copied) {
                e.currentTarget.style.backgroundColor = "transparent";
                e.currentTarget.style.color = "var(--color-text-on-ink-muted)";
              }
            }}
          >
            <AnimatePresence mode="wait" initial={false}>
              {copied ? (
                <motion.svg
                  key="check"
                  initial={{ scale: 0.6, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  exit={{ scale: 0.6, opacity: 0 }}
                  transition={{ duration: 0.15, ease: EASE }}
                  className="h-4 w-4"
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
                  transition={{ duration: 0.15, ease: EASE }}
                  className="h-4 w-4"
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
        </div>
      </div>

      {/* Polite live region for screen readers */}
      <div role="status" aria-live="polite" className="sr-only">
        {copied ? "Copied to clipboard" : ""}
      </div>
    </div>
  );
}
