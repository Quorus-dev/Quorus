import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import CursorGlow from "@/components/CursorGlow";
import AnnouncementBar from "@/components/AnnouncementBar";

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
      <body
        className="min-h-full text-white"
        style={{ background: "var(--background)" }}
      >
        <CursorGlow />
        <AnnouncementBar />
        {children}
      </body>
    </html>
  );
}
