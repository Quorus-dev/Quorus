/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // ── New design system (Quorus v1) ────────────────────────────
        cream: {
          DEFAULT: "var(--color-cream)",
          50: "#faf7f0",
          100: "#f5f1ea",
          200: "#ebe5d6",
          300: "#ddd5c0",
        },
        ink: {
          DEFAULT: "var(--color-ink)",
          50: "#4a4a52",
          100: "#2a2a32",
          200: "#14141c",
          300: "#0a0a0f",
        },
        accent: {
          DEFAULT: "var(--color-accent)",
          on: "var(--color-accent-on-ink)",
          700: "#0d4d4a",
          500: "#2e7d77",
          300: "#5eb3a8",
        },
        slate: {
          300: "#a8a8b0",
          400: "#7a7a82",
          500: "#6a6a72",
          600: "#4a4a52",
        },
        // ── Legacy aliases (kept so unused-but-not-yet-deleted components
        // still compile during transition; remove once dead-code sweep is done)
        background: "var(--background)",
        foreground: "var(--foreground)",
        muted: "var(--muted)",
        border: "var(--border)",
        surface: "var(--surface)",
        accent2: "var(--accent2)",
      },
      fontFamily: {
        sans: [
          "Plus Jakarta Sans",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SF Mono",
          "Menlo",
          "monospace",
        ],
      },
      borderRadius: {
        DEFAULT: "12px",
        md: "12px",
        lg: "16px",
      },
      transitionTimingFunction: {
        "out-expo": "cubic-bezier(0.16, 1, 0.3, 1)",
      },
      letterSpacing: {
        tight2: "-0.022em",
        widest2: "0.18em",
      },
      maxWidth: {
        prose2: "62ch",
      },
      boxShadow: {
        "card-hover": "0 8px 24px rgba(13, 77, 74, 0.06)",
      },
    },
  },
  plugins: [],
};
