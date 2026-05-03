import { motion } from "framer-motion";

const COLORS = {
  ink: "#0a0a0f",
  ink2: "#14141c",
  borderDark: "rgba(255,255,255,0.08)",
  borderDarkStrong: "rgba(255,255,255,0.12)",
  textPrimary: "#f5f1ea",
  textSecondary: "#a8a8b0",
  textMuted: "#6a6a72",
  accentOnInk: "#5eb3a8",
} as const;

const EASE = [0.16, 1, 0.3, 1] as const;
const MONO = "'JetBrains Mono', ui-monospace, monospace";
const SANS = "'Plus Jakarta Sans', system-ui, sans-serif";

const NOISE_SVG =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'>
      <filter id='n'>
        <feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/>
        <feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.06 0'/>
      </filter>
      <rect width='100%' height='100%' filter='url(#n)'/>
    </svg>`,
  );

type CellValue = string | boolean;

interface FeatureRow {
  feature: string;
  free: CellValue;
  pro: CellValue;
  enterprise: CellValue;
}

interface FeatureGroup {
  title: string;
  rows: FeatureRow[];
}

const GROUPS: FeatureGroup[] = [
  {
    title: "Coordination",
    rows: [
      {
        feature: "Active rooms",
        free: "3",
        pro: "50",
        enterprise: "Unlimited",
      },
      {
        feature: "Agents per room",
        free: "5",
        pro: "25",
        enterprise: "Unlimited",
      },
      {
        feature: "Concurrent sessions",
        free: "10",
        pro: "500",
        enterprise: "Unlimited",
      },
      { feature: "Distributed locks", free: true, pro: true, enterprise: true },
      {
        feature: "Shared state matrix",
        free: true,
        pro: true,
        enterprise: true,
      },
    ],
  },
  {
    title: "Delivery & Tools",
    rows: [
      {
        feature: "MCP tools",
        free: "12",
        pro: "12 + custom",
        enterprise: "Custom + private",
      },
      {
        feature: "SSE event delivery",
        free: "Best-effort",
        pro: "99.9% SLA",
        enterprise: "99.99% SLA",
      },
      {
        feature: "Message retention",
        free: "24 hours",
        pro: "30 days",
        enterprise: "Custom",
      },
      {
        feature: "Audit log + replay",
        free: false,
        pro: true,
        enterprise: true,
      },
    ],
  },
  {
    title: "Security & Admin",
    rows: [
      { feature: "Workspace admin", free: false, pro: true, enterprise: true },
      {
        feature: "SSO (SAML / OIDC)",
        free: false,
        pro: false,
        enterprise: true,
      },
      {
        feature: "Private relay deploy",
        free: "Self-host",
        pro: "Self-host",
        enterprise: "Dedicated",
      },
      {
        feature: "Data residency",
        free: "US",
        pro: "US / EU",
        enterprise: "Any region",
      },
      { feature: "BYO API keys", free: true, pro: true, enterprise: true },
    ],
  },
  {
    title: "Support",
    rows: [
      {
        feature: "Support tier",
        free: "Community",
        pro: "Email · 1 day",
        enterprise: "Dedicated",
      },
      {
        feature: "Onboarding",
        free: "Docs",
        pro: "Async review",
        enterprise: "White-glove",
      },
      { feature: "Custom contract", free: false, pro: false, enterprise: true },
    ],
  },
];

function CheckMark() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      aria-label="Included"
      role="img"
    >
      <path
        d="M3.5 8.5l3 3 6-6"
        stroke={COLORS.accentOnInk}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DashMark() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      aria-label="Not included"
      role="img"
    >
      <path
        d="M4 8h8"
        stroke={COLORS.textMuted}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function Cell({ value }: { value: CellValue }) {
  if (typeof value === "boolean") {
    return (
      <span className="inline-flex items-center justify-center">
        {value ? <CheckMark /> : <DashMark />}
      </span>
    );
  }
  return (
    <span
      className="text-[12.5px]"
      style={{ color: COLORS.textPrimary, fontFamily: MONO }}
    >
      {value}
    </span>
  );
}

export default function PricingTable() {
  return (
    <section
      data-theme="dark"
      aria-labelledby="comparison-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: COLORS.ink }}
    >
      {/* Off-center radial — same family as ControlCenterDark */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 80% 55% at 30% 20%, rgba(94,179,168,0.08), transparent 70%)",
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage: `url("${NOISE_SVG}")`,
          backgroundSize: "200px 200px",
          mixBlendMode: "overlay",
        }}
      />

      <div className="relative mx-auto max-w-7xl px-6 py-24 md:py-32">
        {/* Header */}
        <div className="mx-auto max-w-3xl text-center">
          <motion.p
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.3 }}
            transition={{ duration: 0.6, ease: EASE }}
            className="text-[11px] uppercase"
            style={{
              color: COLORS.accentOnInk,
              fontFamily: MONO,
              letterSpacing: "0.22em",
            }}
          >
            Compare plans
          </motion.p>
          <motion.h2
            id="comparison-heading"
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.3 }}
            transition={{ duration: 0.6, ease: EASE, delay: 0.05 }}
            className="mt-4 text-balance"
            style={{
              color: COLORS.textPrimary,
              fontFamily: SANS,
              fontSize: "clamp(36px, 4.5vw, 56px)",
              fontWeight: 600,
              lineHeight: 1.05,
              letterSpacing: "-0.02em",
            }}
          >
            Every feature, side by side.
          </motion.h2>
          <motion.p
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.3 }}
            transition={{ duration: 0.6, ease: EASE, delay: 0.1 }}
            className="mx-auto mt-5 max-w-xl text-pretty"
            style={{
              color: COLORS.textSecondary,
              fontFamily: SANS,
              fontSize: 16,
              lineHeight: 1.6,
            }}
          >
            The relay primitives are the same on every tier — what changes is
            scale, residency, and the team that picks up the phone.
          </motion.p>
        </div>

        {/* Table — horizontal scroll on mobile with sticky first column */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.15 }}
          transition={{ duration: 0.7, ease: EASE, delay: 0.15 }}
          className="mt-14 overflow-x-auto"
          style={{
            border: `1px solid ${COLORS.borderDark}`,
            borderRadius: 12,
            backgroundColor: COLORS.ink2,
          }}
        >
          <table
            className="w-full min-w-[640px] border-collapse"
            style={{ fontFamily: SANS }}
          >
            <thead>
              <tr>
                <th
                  scope="col"
                  className="sticky left-0 z-10 px-5 py-4 text-left text-[11px] uppercase"
                  style={{
                    color: COLORS.textMuted,
                    fontFamily: MONO,
                    letterSpacing: "0.18em",
                    backgroundColor: COLORS.ink2,
                    borderBottom: `1px solid ${COLORS.borderDarkStrong}`,
                    minWidth: 220,
                  }}
                >
                  Feature
                </th>
                {(["Free", "Pro", "Enterprise"] as const).map((tier) => (
                  <th
                    key={tier}
                    scope="col"
                    className="px-5 py-4 text-center text-[11px] uppercase"
                    style={{
                      color:
                        tier === "Pro" ? COLORS.accentOnInk : COLORS.textMuted,
                      fontFamily: MONO,
                      letterSpacing: "0.18em",
                      borderBottom: `1px solid ${COLORS.borderDarkStrong}`,
                      minWidth: 140,
                    }}
                  >
                    {tier}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {GROUPS.map((group, gIdx) => (
                <Group
                  key={group.title}
                  group={group}
                  firstGroup={gIdx === 0}
                />
              ))}
            </tbody>
          </table>
        </motion.div>

        <p
          className="mt-6 text-center text-[12px]"
          style={{
            color: COLORS.textMuted,
            fontFamily: MONO,
            letterSpacing: "0.04em",
          }}
        >
          Self-hosted relay is free forever — every primitive, every tool.
        </p>
      </div>
    </section>
  );
}

function Group({
  group,
  firstGroup,
}: {
  group: FeatureGroup;
  firstGroup: boolean;
}) {
  return (
    <>
      <tr>
        <th
          scope="rowgroup"
          colSpan={4}
          className="sticky left-0 z-10 px-5 pb-3 text-left text-[10.5px] uppercase"
          style={{
            color: COLORS.accentOnInk,
            fontFamily: MONO,
            letterSpacing: "0.22em",
            backgroundColor: COLORS.ink2,
            paddingTop: firstGroup ? 24 : 32,
            borderTop: firstGroup ? "none" : `1px solid ${COLORS.borderDark}`,
          }}
        >
          {group.title}
        </th>
      </tr>
      {group.rows.map((row, rIdx) => (
        <tr key={row.feature}>
          <th
            scope="row"
            className="sticky left-0 z-10 px-5 py-3.5 text-left text-[13px] font-normal"
            style={{
              color: COLORS.textSecondary,
              fontFamily: SANS,
              backgroundColor: COLORS.ink2,
              borderTop: rIdx === 0 ? "none" : `1px solid ${COLORS.borderDark}`,
            }}
          >
            {row.feature}
          </th>
          <td
            className="px-5 py-3.5 text-center"
            style={{
              borderTop: rIdx === 0 ? "none" : `1px solid ${COLORS.borderDark}`,
            }}
          >
            <Cell value={row.free} />
          </td>
          <td
            className="px-5 py-3.5 text-center"
            style={{
              borderTop: rIdx === 0 ? "none" : `1px solid ${COLORS.borderDark}`,
              backgroundColor: "rgba(94,179,168,0.03)",
            }}
          >
            <Cell value={row.pro} />
          </td>
          <td
            className="px-5 py-3.5 text-center"
            style={{
              borderTop: rIdx === 0 ? "none" : `1px solid ${COLORS.borderDark}`,
            }}
          >
            <Cell value={row.enterprise} />
          </td>
        </tr>
      ))}
    </>
  );
}
