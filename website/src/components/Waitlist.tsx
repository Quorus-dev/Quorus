import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

export default function Waitlist({
  size = "md",
  className = "",
  placeholder = "you@example.com",
  label = "Request access",
}: {
  size?: "sm" | "md" | "lg";
  className?: string;
  placeholder?: string;
  label?: string;
}) {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "done" | "error">(
    "idle",
  );

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || status !== "idle") return;
    setStatus("loading");
    try {
      const res = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) throw new Error();
      setStatus("done");
    } catch {
      setStatus("error");
      setTimeout(() => setStatus("idle"), 3000);
    }
  };

  const pad = size === "lg" ? "px-5 py-4" : "px-4 py-3";
  const text = size === "lg" ? "text-base" : "text-sm";

  return (
    <AnimatePresence mode="wait">
      {status === "done" ? (
        <motion.div
          key="success"
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className={`flex flex-col items-center gap-2 ${className}`}
        >
          <div className="flex items-center gap-2.5 px-5 py-3 rounded-xl bg-green-500/10 border border-green-500/20">
            <svg
              className="w-4 h-4 text-green-400 shrink-0"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M5 13l4 4L19 7"
              />
            </svg>
            <span className="text-sm text-green-300 font-medium">
              You&apos;re on the list.
            </span>
          </div>
          <p className="text-xs text-white/30">
            We review every application. We&apos;ll be in touch.
          </p>
        </motion.div>
      ) : (
        <motion.form
          key="form"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          onSubmit={submit}
          className={`flex flex-col sm:flex-row gap-2.5 w-full ${className}`}
        >
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder={placeholder}
            className={`flex-1 ${pad} ${text} rounded-xl bg-white/[0.05] border border-white/10 text-white placeholder:text-white/25 outline-none focus:border-teal-500/50 focus:ring-2 focus:ring-teal-500/10 transition-all duration-200`}
          />
          <button
            type="submit"
            disabled={status === "loading"}
            className={`${pad} ${text} rounded-xl bg-teal-600 hover:bg-teal-500 disabled:opacity-60 disabled:cursor-not-allowed text-white font-medium transition-all duration-200 hover:shadow-lg hover:shadow-teal-500/20 whitespace-nowrap`}
          >
            {status === "loading" ? (
              <span className="flex items-center gap-2 justify-center">
                <svg
                  className="w-4 h-4 animate-spin"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                  />
                </svg>
                Joining…
              </span>
            ) : (
              label
            )}
          </button>
          {status === "error" && (
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-xs text-red-400 text-center sm:text-left"
            >
              Something went wrong. Please try again.
            </motion.p>
          )}
        </motion.form>
      )}
    </AnimatePresence>
  );
}
