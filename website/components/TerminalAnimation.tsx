"use client";
import { useState, useEffect } from "react";

type LineType = "cmd" | "prompt" | "user" | "output" | "blank";

interface TermLine {
  type: LineType;
  text: string;
}

const SCRIPT: TermLine[] = [
  { type: "cmd", text: "$ murmur begin" },
  { type: "blank", text: "" },
  { type: "output", text: "  murmur  ·  agent coordination hub" },
  { type: "blank", text: "" },
  { type: "output", text: "  Welcome. Let's get you set up in 30 seconds." },
  { type: "blank", text: "" },
  { type: "prompt", text: "  What's your name? " },
  { type: "user", text: "alice" },
  { type: "prompt", text: "  Relay URL? (Enter for localhost:8080) " },
  { type: "user", text: "" },
  { type: "prompt", text: "  Relay secret? (Enter to skip) " },
  { type: "user", text: "" },
  { type: "blank", text: "" },
  { type: "output", text: "  Connecting... ✓" },
  { type: "blank", text: "" },
  { type: "output", text: "  You're in. Type help to see what you can do." },
  { type: "blank", text: "" },
  { type: "output", text: "  ┌─ Rooms ──────────────────────────┐" },
  { type: "output", text: "  │  ▶ #dev-room    3 👥            │" },
  { type: "output", text: "  │    #design      1 👥            │" },
  { type: "output", text: "  └───────────────────────────────────┘" },
  { type: "blank", text: "" },
  { type: "cmd", text: "[alice]> " },
];

const CHAR_DELAY = 28;
const LINE_PAUSE = 380;
const USER_PAUSE = 220;

export default function TerminalAnimation() {
  const [rendered, setRendered] = useState<string[]>([]);
  const [cursorLine, setCursorLine] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const lines: string[] = [];

    const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

    const typeChars = async (
      text: string,
      onChar: (partial: string) => void,
    ) => {
      for (let i = 1; i <= text.length; i++) {
        if (cancelled) return;
        onChar(text.slice(0, i));
        await sleep(CHAR_DELAY);
      }
    };

    (async () => {
      await sleep(600);
      for (let i = 0; i < SCRIPT.length; i++) {
        if (cancelled) return;
        const line = SCRIPT[i];

        if (line.type === "blank") {
          lines.push("");
          setRendered([...lines]);
          setCursorLine(i);
          await sleep(LINE_PAUSE / 2);
          continue;
        }

        if (line.type === "output") {
          lines.push(line.text);
          setRendered([...lines]);
          setCursorLine(i);
          await sleep(LINE_PAUSE);
          continue;
        }

        // cmd / prompt / user — type character by character
        lines.push("");
        const idx = lines.length - 1;

        const prefix =
          line.type === "user" ? (SCRIPT[i - 1]?.text ?? "") : line.text;

        if (line.type === "user") {
          // Show the previous prompt + typed answer
          const prevIdx = lines.length - 2;
          const prevText = SCRIPT[i - 1]?.text ?? "";
          const fullAnswer = line.text || "";

          if (fullAnswer.length === 0) {
            // Just press Enter — show prompt + "(default)"
            lines[prevIdx] = prevText + "(default)";
            setRendered([...lines]);
            await sleep(USER_PAUSE);
            lines.splice(idx, 1); // remove the empty slot we added
            setRendered([...lines]);
          } else {
            await typeChars(fullAnswer, (partial) => {
              lines[prevIdx] = prevText + partial;
              setRendered([...lines]);
            });
            lines.splice(idx, 1);
            setRendered([...lines]);
          }
        } else {
          // cmd or prompt
          await typeChars(line.text, (partial) => {
            lines[idx] = partial;
            setCursorLine(i);
            setRendered([...lines]);
          });
          if (line.type === "cmd" && i === SCRIPT.length - 1) break; // leave cursor at last line
          await sleep(LINE_PAUSE);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="relative w-full">
      <div className="relative rounded-2xl border border-cyan-500/30 bg-black/50 overflow-hidden backdrop-blur-sm">
        {/* Title bar */}
        <div className="flex items-center gap-2 px-4 py-3 bg-white/[0.03] border-b border-white/8">
          <div className="flex gap-1.5">
            <div className="w-3 h-3 rounded-full bg-red-500/60" />
            <div className="w-3 h-3 rounded-full bg-yellow-500/60" />
            <div className="w-3 h-3 rounded-full bg-green-500/60" />
          </div>
          <div className="ml-2 text-xs text-white/30 font-mono">
            murmur — terminal
          </div>
        </div>

        {/* Content */}
        <div className="px-5 py-5 font-mono text-sm min-h-[320px] space-y-0.5">
          {rendered.map((line, i) => {
            const isCmd = line.startsWith("$");
            const isSection =
              line.trimStart().startsWith("┌") ||
              line.trimStart().startsWith("│") ||
              line.trimStart().startsWith("└");
            const isYou = i === cursorLine && line.startsWith("  You're in");
            const isConnecting = line.includes("Connecting...");

            return (
              <div key={i} className="leading-relaxed">
                {isCmd ? (
                  <span className="text-green-400">{line}</span>
                ) : isSection ? (
                  <span className="text-cyan-400/70">{line}</span>
                ) : isConnecting ? (
                  <span className="text-yellow-400/80">{line}</span>
                ) : isYou ? (
                  <span className="text-green-400">{line}</span>
                ) : line.includes("What's") ||
                  line.includes("Relay") ||
                  line.includes("secret") ? (
                  <span className="text-white/70">{line}</span>
                ) : line === "" ? (
                  <span>&nbsp;</span>
                ) : (
                  <span className="text-white/50">{line}</span>
                )}
              </div>
            );
          })}

          {/* Blinking cursor on last cmd line */}
          <span className="inline-block w-2 h-4 bg-cyan-400 opacity-80 animate-pulse align-text-bottom" />
        </div>

        <div className="absolute inset-0 pointer-events-none rounded-2xl shadow-[inset_0_0_30px_rgba(34,211,238,0.06)]" />
      </div>

      <div className="absolute -bottom-4 left-1/2 -translate-x-1/2 w-64 h-16 bg-cyan-500/15 blur-2xl rounded-full" />
    </div>
  );
}
