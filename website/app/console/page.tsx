import type { Metadata } from "next";
import MurmurConsole from "@/components/MurmurConsole";

export const metadata: Metadata = {
  title: "Murmur Console — Monitor Your Agent Swarm",
  description:
    "Connect to your Murmur relay, watch rooms in real-time, and send messages to your agent swarm.",
};

export default function ConsolePage() {
  return <MurmurConsole />;
}
