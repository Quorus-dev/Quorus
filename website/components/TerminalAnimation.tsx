"use client";
import { useState, useEffect } from "react";

const COMMANDS = [
  'pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"',
  "murmur init alice --relay https://relay.murmur.dev --secret your-secret",
  "# Restart Claude Code — Murmur appears as MCP tools",
];

const DELAY_BEFORE_START = 500; // ms before animation starts
const CHAR_DELAY = 30; // ms per character
const LINE_DELAY = 800; // ms between lines

export default function TerminalAnimation() {
  const [displayedText, setDisplayedText] = useState("");
  const [currentLine, setCurrentLine] = useState(0);
  const [currentChar, setCurrentChar] = useState(0);
  const [isComplete, setIsComplete] = useState(false);

  useEffect(() => {
    if (isComplete) return;

    if (currentLine >= COMMANDS.length) {
      setIsComplete(true);
      return;
    }

    const currentCommand = COMMANDS[currentLine];

    // Determine what text to show
    let newText = COMMANDS.slice(0, currentLine).join("\n");
    if (newText) newText += "\n";

    if (currentChar < currentCommand.length) {
      // Still typing current line
      const timer = setTimeout(
        () => {
          newText += currentCommand.slice(0, currentChar + 1);
          setDisplayedText(newText);
          setCurrentChar(currentChar + 1);
        },
        currentChar === 0 ? DELAY_BEFORE_START : CHAR_DELAY,
      );

      return () => clearTimeout(timer);
    } else {
      // Move to next line
      newText += currentCommand;
      setDisplayedText(newText);

      const timer = setTimeout(() => {
        setCurrentLine(currentLine + 1);
        setCurrentChar(0);
      }, LINE_DELAY);

      return () => clearTimeout(timer);
    }
  }, [currentLine, currentChar, isComplete]);

  return (
    <div className="relative w-full">
      {/* Terminal container */}
      <div className="relative rounded-2xl border border-cyan-500/30 bg-black/40 overflow-hidden backdrop-blur-sm">
        {/* Terminal header */}
        <div className="flex items-center gap-2 px-4 py-3 bg-white/5 border-b border-white/10">
          <div className="flex gap-1.5">
            <div className="w-3 h-3 rounded-full bg-red-500/60" />
            <div className="w-3 h-3 rounded-full bg-yellow-500/60" />
            <div className="w-3 h-3 rounded-full bg-green-500/60" />
          </div>
          <div className="ml-2 text-xs text-white/40 font-mono">terminal</div>
        </div>

        {/* Terminal content */}
        <div className="px-6 py-4 font-mono text-sm text-green-400 min-h-[200px] relative">
          <pre className="whitespace-pre-wrap break-words">{displayedText}</pre>

          {/* Blinking cursor */}
          {!isComplete && <span className="animate-pulse">▌</span>}

          {/* Completion message */}
          {isComplete && (
            <div className="mt-4 text-cyan-400/60 text-xs animate-fade-in">
              ✨ Ready to coordinate with Murmur
            </div>
          )}
        </div>

        {/* Glow effect */}
        <div className="absolute inset-0 pointer-events-none rounded-2xl shadow-[inset_0_0_20px_rgba(34,211,238,0.1)]" />
      </div>

      {/* Bottom glow */}
      <div className="absolute -bottom-4 left-1/2 -translate-x-1/2 w-64 h-16 bg-cyan-500/20 blur-2xl rounded-full opacity-50" />
    </div>
  );
}
