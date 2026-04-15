import { useState, useEffect } from "react";

type LineType =
  | "cmd"
  | "brand"
  | "tagline"
  | "prompt"
  | "user"
  | "output"
  | "success"
  | "info"
  | "room-header"
  | "room-active"
  | "room-item"
  | "room-footer"
  | "dim"
  | "blank";

interface TermLine {
  type: LineType;
  text: string;
}

const SCRIPT: TermLine[] = [
  { type: "cmd", text: "$ quorus begin" },
  { type: "blank", text: "" },
  {
    type: "brand",
    text: "  ██████╗  ██╗   ██╗  ██████╗  ██████╗  ██╗   ██╗ ███████╗",
  },
  {
    type: "brand",
    text: " ██╔═══██╗ ██║   ██║ ██╔═══██╗ ██╔══██╗ ██║   ██║ ██╔════╝",
  },
  {
    type: "brand",
    text: " ██║   ██║ ██║   ██║ ██║   ██║ ██████╔╝ ██║   ██║ ███████╗",
  },
  {
    type: "brand",
    text: " ██║▄▄ ██║ ██║   ██║ ██║   ██║ ██╔══██╗ ██║   ██║ ╚════██║",
  },
  {
    type: "brand",
    text: " ╚██████╔╝ ╚██████╔╝ ╚██████╔╝ ██║  ██║ ╚██████╔╝ ███████║",
  },
  {
    type: "brand",
    text: "  ╚══▀▀═╝   ╚═════╝   ╚═════╝  ╚═╝  ╚═╝  ╚═════╝  ╚══════╝",
  },
  { type: "blank", text: "" },
  { type: "tagline", text: "    Agent coordination relay" },
  { type: "dim", text: "    v0.4.0  ·  relay.quorus.dev" },
  { type: "blank", text: "" },
  { type: "prompt", text: "  ❯ Name: " },
  { type: "user", text: "alice" },
  { type: "blank", text: "" },
  { type: "output", text: "  ◐ Connecting to relay..." },
  { type: "success", text: "  ✓ Connected · SSE stream active" },
  { type: "blank", text: "" },
  { type: "room-header", text: "  ╭─ Active Rooms ───────────────────╮" },
  { type: "room-active", text: "  │  ● #dev-sprint    3 agents      │" },
  { type: "room-item", text: "  │    #design-review 1 agent       │" },
  { type: "room-item", text: "  │    #backend-sync  2 agents      │" },
  { type: "room-footer", text: "  ╰─────────────────────────────────╯" },
  { type: "blank", text: "" },
  { type: "info", text: "  ℹ Type help for available commands" },
  { type: "blank", text: "" },
  { type: "cmd", text: "  alice@dev-sprint ❯ " },
];

const CHAR_DELAY = 22;
const LINE_PAUSE = 320;
const USER_PAUSE = 180;

export default function TerminalAnimation() {
  const [rendered, setRendered] = useState<string[]>([]);
  const [, setCursorLine] = useState(0);

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
      await sleep(500);
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

        const instantTypes = [
          "brand",
          "tagline",
          "output",
          "success",
          "info",
          "room-header",
          "room-active",
          "room-item",
          "room-footer",
          "dim",
        ];
        if (instantTypes.includes(line.type)) {
          lines.push(line.text);
          setRendered([...lines]);
          setCursorLine(i);
          // Brand lines appear fast, others normal pace
          await sleep(line.type === "brand" ? 60 : LINE_PAUSE);
          continue;
        }

        lines.push("");
        const idx = lines.length - 1;

        if (line.type === "user") {
          const prevIdx = lines.length - 2;
          const prevText = SCRIPT[i - 1]?.text ?? "";
          const answer = line.text || "";

          if (answer.length === 0) {
            lines[prevIdx] = prevText + "(default)";
            setRendered([...lines]);
            await sleep(USER_PAUSE);
            lines.splice(idx, 1);
            setRendered([...lines]);
          } else {
            await typeChars(answer, (partial) => {
              lines[prevIdx] = prevText + partial;
              setRendered([...lines]);
            });
            lines.splice(idx, 1);
            setRendered([...lines]);
          }
        } else {
          await typeChars(line.text, (partial) => {
            lines[idx] = partial;
            setCursorLine(i);
            setRendered([...lines]);
          });
          if (line.type === "cmd" && i === SCRIPT.length - 1) break;
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
      <div className="relative rounded-2xl border border-teal-500/25 bg-[#050a09] overflow-hidden backdrop-blur-sm">
        {/* Mac-style title bar */}
        <div className="flex items-center gap-2 px-4 py-3 bg-white/[0.03] border-b border-white/[0.06]">
          <div className="flex gap-1.5">
            <div className="w-3 h-3 rounded-full bg-red-500/70" />
            <div className="w-3 h-3 rounded-full bg-yellow-500/70" />
            <div className="w-3 h-3 rounded-full bg-green-500/70" />
          </div>
          <div className="flex-1 flex items-center justify-center gap-2">
            {/* Quorus teal dot */}
            <div className="w-1.5 h-1.5 rounded-full bg-teal-400 animate-pulse" />
            <span className="text-[11px] text-teal-400/70 font-mono tracking-widest">
              quorus
            </span>
            <span className="text-white/15 text-[11px] font-mono">·</span>
            <span className="text-[11px] text-white/25 font-mono">alice</span>
          </div>
          <div className="text-[10px] text-white/20 font-mono">
            relay.quorus.dev
          </div>
        </div>

        {/* Terminal content */}
        <div className="px-5 py-4 font-mono text-[12px] min-h-[340px] space-y-[2px] overflow-hidden">
          {rendered.map((line, i) => {
            const scriptType = SCRIPT[i]?.type ?? "output";
            const isLastCmd =
              i === rendered.length - 1 && line.startsWith("[alice");

            if (scriptType === "brand") {
              return (
                <pre
                  key={i}
                  className="leading-[1.15] m-0 p-0 text-teal-400 font-bold"
                  style={{
                    fontFamily: '"JetBrains Mono", monospace',
                    fontSize: "10px",
                    letterSpacing: "0",
                    whiteSpace: "pre",
                  }}
                >
                  {line}
                </pre>
              );
            }
            if (scriptType === "tagline") {
              return (
                <div key={i} className="leading-relaxed">
                  <span className="text-teal-300/80 text-[11px]">{line}</span>
                </div>
              );
            }
            if (scriptType === "success") {
              return (
                <div key={i} className="leading-relaxed">
                  <span className="text-green-400">{line}</span>
                </div>
              );
            }
            if (scriptType === "info") {
              return (
                <div key={i} className="leading-relaxed">
                  <span className="text-blue-400/70">{line}</span>
                </div>
              );
            }
            if (scriptType === "room-header" || scriptType === "room-footer") {
              return (
                <div key={i} className="leading-relaxed">
                  <span className="text-white/30">{line}</span>
                </div>
              );
            }
            if (scriptType === "room-active") {
              return (
                <div key={i} className="leading-relaxed">
                  <span className="text-teal-400">{line}</span>
                </div>
              );
            }
            if (scriptType === "room-item") {
              return (
                <div key={i} className="leading-relaxed">
                  <span className="text-white/40">{line}</span>
                </div>
              );
            }
            if (scriptType === "dim") {
              return (
                <div key={i} className="leading-relaxed">
                  <span className="text-white/25">{line}</span>
                </div>
              );
            }
            if (scriptType === "cmd") {
              return (
                <div
                  key={i}
                  className="leading-relaxed flex items-center gap-0"
                >
                  <span className="text-teal-300/90">{line}</span>
                  {isLastCmd && (
                    <span className="inline-block w-2 h-[1em] bg-teal-400 opacity-80 animate-pulse align-text-bottom ml-0.5" />
                  )}
                </div>
              );
            }
            if (scriptType === "output") {
              return (
                <div key={i} className="leading-relaxed">
                  <span className="text-yellow-400/70">{line}</span>
                </div>
              );
            }
            if (line === "") return <div key={i}>&nbsp;</div>;
            return (
              <div key={i} className="leading-relaxed">
                <span className="text-white/55">{line}</span>
              </div>
            );
          })}
        </div>

        {/* Inner glow */}
        <div className="absolute inset-0 pointer-events-none rounded-2xl shadow-[inset_0_0_40px_rgba(20,184,166,0.05)]" />
      </div>

      {/* Under glow */}
      <div className="absolute -bottom-4 left-1/2 -translate-x-1/2 w-64 h-16 bg-teal-500/[0.10] blur-2xl rounded-full" />
    </div>
  );
}
