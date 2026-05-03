import { type ReactNode } from "react";
import { motion, useReducedMotion } from "framer-motion";

/**
 * Shared prose primitives for /docs pages.
 *
 * The design contract: cream background, Plus Jakarta Sans body, JetBrains Mono
 * for inline code, accent-bordered blockquotes for pull-quotes. All sizing,
 * line-height, and color come from CSS variables defined in tokens.css.
 *
 * Each primitive is a thin styled wrapper so the docs pages stay legible at
 * a glance and the design system stays single-sourced.
 */

const EASE = [0.16, 1, 0.3, 1] as const;

export function DocsArticleHeader({
  eyebrow,
  title,
  lead,
}: {
  eyebrow: string;
  title: string;
  lead?: string;
}) {
  const prefersReduced = useReducedMotion();
  return (
    <motion.header
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: prefersReduced ? 0 : 0.6, ease: EASE }}
      className="mb-10"
    >
      <p
        className="mb-4 font-mono text-[11px] uppercase tracking-[0.18em]"
        style={{ color: "var(--color-accent)" }}
      >
        {eyebrow}
      </p>
      <h1
        style={{
          color: "var(--color-text-on-cream)",
          fontSize: "clamp(36px, 4.5vw, 52px)",
          lineHeight: 1.05,
          fontWeight: 600,
          letterSpacing: "-0.022em",
        }}
      >
        {title}
      </h1>
      {lead ? (
        <p
          className="mt-5 max-w-prose2 text-[17px] leading-[1.6]"
          style={{ color: "var(--color-text-on-cream-secondary)" }}
        >
          {lead}
        </p>
      ) : null}
    </motion.header>
  );
}

export function DocsH2({ children }: { children: ReactNode }) {
  return (
    <h2
      className="mb-3 mt-12 text-[26px] font-semibold tracking-tight"
      style={{
        color: "var(--color-text-on-cream)",
        letterSpacing: "-0.018em",
        lineHeight: 1.2,
      }}
    >
      {children}
    </h2>
  );
}

export function DocsH3({ children }: { children: ReactNode }) {
  return (
    <h3
      className="mb-2 mt-9 text-[18px] font-semibold tracking-tight"
      style={{
        color: "var(--color-text-on-cream)",
        letterSpacing: "-0.012em",
        lineHeight: 1.3,
      }}
    >
      {children}
    </h3>
  );
}

export function DocsLead({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <p
      className={`mt-5 max-w-prose2 text-[17px] leading-[1.6] ${className}`}
      style={{ color: "var(--color-text-on-cream-secondary)" }}
    >
      {children}
    </p>
  );
}

export function DocsP({ children }: { children: ReactNode }) {
  return (
    <p
      className="mb-4 text-[16px] leading-[1.6]"
      style={{ color: "var(--color-text-on-cream-secondary)" }}
    >
      {children}
    </p>
  );
}

export function DocsInlineCode({ children }: { children: ReactNode }) {
  return (
    <code
      className="rounded px-1.5 py-0.5 font-mono text-[0.88em]"
      style={{
        color: "var(--color-text-on-cream)",
        backgroundColor: "var(--color-cream-2)",
        border: "1px solid var(--color-border-light)",
      }}
    >
      {children}
    </code>
  );
}

export function DocsList({ items }: { items: ReactNode[] }) {
  return (
    <ul className="mb-5 ml-5 list-disc space-y-2 text-[16px] leading-[1.6] marker:text-[var(--color-text-on-cream-muted)]">
      {items.map((item, i) => (
        <li key={i} style={{ color: "var(--color-text-on-cream-secondary)" }}>
          {item}
        </li>
      ))}
    </ul>
  );
}

export function DocsNote({ children }: { children: ReactNode }) {
  return (
    <aside
      className="my-5 rounded-md py-3 pl-4 pr-4 text-[14.5px] leading-[1.55]"
      style={{
        backgroundColor: "rgba(13,77,74,0.04)",
        borderLeft: "2px solid var(--color-accent)",
        color: "var(--color-text-on-cream-secondary)",
      }}
    >
      {children}
    </aside>
  );
}

export function DocsBlockquote({ children }: { children: ReactNode }) {
  return (
    <blockquote
      className="my-7 py-2 pl-5 pr-2 text-[18px] italic leading-[1.55]"
      style={{
        borderLeft: "2px solid var(--color-accent)",
        color: "var(--color-text-on-cream)",
        letterSpacing: "-0.005em",
      }}
    >
      {children}
    </blockquote>
  );
}

export function DocsNextSteps({ children }: { children: ReactNode }) {
  return (
    <section
      className="mt-12 rounded-md p-6"
      style={{
        backgroundColor: "rgba(255,255,255,0.5)",
        border: "1px solid var(--color-border-light)",
      }}
    >
      <p
        className="mb-3 font-mono text-[10.5px] uppercase tracking-[0.2em]"
        style={{ color: "var(--color-text-on-cream-muted)" }}
      >
        Next
      </p>
      <ul className="space-y-2 text-[15px] leading-[1.55]">{children}</ul>
    </section>
  );
}
