import { useCallback, useId, useState } from "react";

interface CodeBlockProps {
  command: string;
  label?: string;
  prompt?: string;
}

export default function CodeBlock({
  command,
  label,
  prompt = "$",
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
        className="group flex items-center gap-3 px-4 py-3 rounded-xl border border-white/10 bg-white/[0.03] font-mono text-xs sm:text-sm overflow-x-auto"
        aria-labelledby={label ? labelId : undefined}
      >
        <span className="text-teal-400 shrink-0" aria-hidden="true">
          {prompt}
        </span>
        <code className="text-white/85 whitespace-nowrap">{command}</code>
        <button
          type="button"
          aria-label={copied ? "Copied to clipboard" : "Copy command"}
          onClick={onCopy}
          className="ml-auto shrink-0 inline-flex items-center justify-center w-8 h-8 rounded-md text-white/55 hover:text-teal-300 hover:bg-white/[0.05] focus-visible:text-teal-300 focus-visible:bg-white/[0.05] focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 transition-colors"
        >
          {copied ? (
            <svg
              className="w-4 h-4 text-teal-300"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M5 13l4 4L19 7"
              />
            </svg>
          ) : (
            <svg
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
            </svg>
          )}
        </button>
      </div>
      {/* Polite, screen-reader-only live region for copy confirmation */}
      <div role="status" aria-live="polite" className="sr-only">
        {copied ? "Copied to clipboard" : ""}
      </div>
    </div>
  );
}
