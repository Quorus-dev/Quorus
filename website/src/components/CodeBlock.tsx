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
    navigator.clipboard
      .writeText(command)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {});
  }, [command]);

  return (
    <div className="w-full">
      {label ? (
        <p
          id={labelId}
          className="text-[11px] font-mono text-white/40 mb-2 tracking-wide uppercase"
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
        <code className="text-white/80 whitespace-nowrap">{command}</code>
        <button
          type="button"
          aria-label="Copy command"
          onClick={onCopy}
          className="ml-auto shrink-0 text-white/40 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 hover:text-teal-300 transition-all focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 rounded"
        >
          {copied ? (
            <span className="text-teal-300 text-[11px]">Copied ✓</span>
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
    </div>
  );
}
