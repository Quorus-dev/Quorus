import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import CursorGlow from "@/components/CursorGlow";
import AnnouncementBar from "@/components/AnnouncementBar";
import ScrollReset from "@/components/ScrollReset";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Murmur — The Communication Layer for AI Swarms",
  description:
    "Any model. Any machine. Real-time coordination. The open-source communication substrate for AI agent swarms — rooms, SSE push, shared state, distributed locks.",
  keywords: [
    "AI agents",
    "multi-agent",
    "Claude",
    "MCP",
    "coordination",
    "swarm",
  ],
  openGraph: {
    title: "Murmur — The Communication Layer for AI Swarms",
    description: "Any model. Any machine. Real-time coordination.",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Murmur — Communication Layer for AI Swarms",
    description: "Any model. Any machine. Real-time coordination.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      {/*
        This script runs synchronously while the HTML is being parsed —
        BEFORE the browser can restore the previous scroll position.
        useEffect is too late (fires after hydration). This is the only
        reliable fix for scroll-restoration on reload.
      */}
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `try{history.scrollRestoration='manual';window.scrollTo(0,0);}catch(e){}`,
          }}
        />
      </head>
      <body
        className="min-h-full text-white"
        style={{ background: "var(--background)" }}
      >
        <ScrollReset />
        <CursorGlow />
        <AnnouncementBar />
        {children}
      </body>
    </html>
  );
}
