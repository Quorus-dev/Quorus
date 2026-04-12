"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";

// ── Types ─────────────────────────────────────────────────────────────────────

type Room = {
  id: string;
  name?: string;
  members?: string[];
};

type Message = {
  id?: string;
  from_name?: string;
  sender?: string;
  content: string;
  timestamp: string;
  message_type?: string;
};

// ── Color system ──────────────────────────────────────────────────────────────

const PALETTE = [
  "#a78bfa", // violet
  "#22d3ee", // cyan
  "#34d399", // emerald
  "#f59e0b", // amber
  "#f472b6", // pink
  "#60a5fa", // blue
  "#4ade80", // green
  "#fb923c", // orange
];
const _colorCache = new Map<string, string>();
let _colorIdx = 0;
function colorFor(name: string): string {
  if (!_colorCache.has(name)) {
    _colorCache.set(name, PALETTE[_colorIdx++ % PALETTE.length]);
  }
  return _colorCache.get(name)!;
}

function ts(raw: string): string {
  try {
    return raw.includes("T") ? raw.slice(11, 16) : raw.slice(0, 5);
  } catch {
    return "--:--";
  }
}

// ── Relay proxy fetch ─────────────────────────────────────────────────────────

async function relayFetch(
  relay: string,
  key: string,
  path: string,
  opts?: RequestInit,
): Promise<Response> {
  return fetch(`/api/relay/${path}`, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      "x-relay-url": relay,
      ...(key ? { "x-relay-key": key } : {}),
      ...(opts?.headers as Record<string, string> | undefined),
    },
  });
}

// ── Connect Modal ─────────────────────────────────────────────────────────────

function ConnectModal({
  onConnect,
}: {
  onConnect: (relay: string, key: string, name: string, remember: boolean) => Promise<void>;
}) {
  const [relay, setRelay] = useState(
    () =>
      (typeof window !== "undefined" && sessionStorage.getItem("mr_relay")) ||
      "",
  );
  // API key is NOT restored from storage by default — security improvement
  const [key, setKey] = useState("");
  const [name, setName] = useState(
    () =>
      (typeof window !== "undefined" && sessionStorage.getItem("mr_name")) ||
      "",
  );
  const [remember, setRemember] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!relay || !name) return;
    setLoading(true);
    setError("");
    try {
      await onConnect(relay.replace(/\/+$/, ""), key, name, remember);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach relay");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="min-h-screen flex items-center justify-center px-4 relative"
      style={{ background: "var(--background)" }}
    >
      <div className="absolute inset-0 grid-bg opacity-40 pointer-events-none" />
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 55% 55% at 50% 55%, rgba(124,58,237,0.14) 0%, transparent 70%)",
        }}
      />

      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="relative w-full max-w-[440px]"
      >
        <div className="animated-border rounded-2xl bg-[#09091a]/95 backdrop-blur-xl p-8">
          {/* Logo */}
          <div className="flex items-center gap-2.5 mb-8">
            <div className="relative flex items-center justify-center">
              <div className="w-2 h-2 rounded-full bg-violet-500 pulse-dot" />
              <div className="absolute w-4 h-4 rounded-full bg-violet-500/20 animate-ping" />
            </div>
            <span className="font-mono text-base font-semibold text-white tracking-tight">
              murmur
            </span>
            <span className="text-[11px] text-white/25 font-mono px-1.5 py-0.5 rounded border border-white/8 bg-white/3">
              console
            </span>
          </div>

          <h1 className="text-[22px] font-bold text-white mb-1.5 tracking-tight">
            Connect to your relay
          </h1>
          <p className="text-sm text-white/35 mb-7 leading-relaxed">
            Monitor rooms, watch agents coordinate, and send messages in
            real-time.
          </p>

          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-[11px] text-white/30 font-mono tracking-widest uppercase mb-1.5">
                Relay URL
              </label>
              <input
                type="url"
                value={relay}
                onChange={(e) => setRelay(e.target.value)}
                placeholder="https://your-relay.railway.app"
                required
                className="w-full px-4 py-3 rounded-xl bg-white/[0.04] border border-white/8 text-white text-sm font-mono placeholder:text-white/18 outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/15 transition-all"
              />
            </div>

            <div>
              <label className="block text-[11px] text-white/30 font-mono tracking-widest uppercase mb-1.5">
                API Key / Secret
              </label>
              <input
                type="password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="sk-… or relay secret"
                className="w-full px-4 py-3 rounded-xl bg-white/[0.04] border border-white/8 text-white text-sm font-mono placeholder:text-white/18 outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/15 transition-all"
              />
            </div>

            <div>
              <label className="block text-[11px] text-white/30 font-mono tracking-widest uppercase mb-1.5">
                Your Name
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="arav"
                required
                className="w-full px-4 py-3 rounded-xl bg-white/[0.04] border border-white/8 text-white text-sm font-mono placeholder:text-white/18 outline-none focus:border-violet-500/50 focus:ring-1 focus:ring-violet-500/15 transition-all"
              />
            </div>

            {/* Security notice */}
            <div className="px-3 py-2.5 rounded-lg bg-amber-500/[0.06] border border-amber-500/15">
              <p className="text-[11px] text-amber-400/80 leading-relaxed">
                <span className="font-semibold">Security note:</span> Your API key is sent through
                this server to your relay. Only connect to relays you control.
              </p>
            </div>

            {/* Remember checkbox */}
            <label className="flex items-center gap-2.5 cursor-pointer group">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="w-4 h-4 rounded bg-white/[0.04] border border-white/15 text-violet-500 focus:ring-violet-500/30 cursor-pointer"
              />
              <span className="text-xs text-white/40 group-hover:text-white/55 transition-colors">
                Remember connection for this session
              </span>
            </label>

            <AnimatePresence>
              {error && (
                <motion.p
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  className="text-sm text-red-400 font-mono"
                >
                  ✕ {error}
                </motion.p>
              )}
            </AnimatePresence>

            <button
              type="submit"
              disabled={loading || !relay || !name}
              className="w-full py-3 rounded-xl bg-violet-600 hover:bg-violet-500 active:bg-violet-700 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold text-sm transition-all hover:shadow-lg hover:shadow-violet-500/20 mt-2"
            >
              {loading ? (
                <span className="flex items-center justify-center gap-2">
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
                  Connecting…
                </span>
              ) : (
                "Connect →"
              )}
            </button>
          </form>

          <div className="mt-6 pt-5 border-t border-white/[0.06]">
            <p className="text-xs text-white/20 text-center">
              No relay?{" "}
              <a
                href="/#quickstart"
                className="text-violet-400 hover:text-violet-300 transition-colors"
              >
                Deploy one in 60 seconds →
              </a>
            </p>
          </div>
        </div>
      </motion.div>
    </div>
  );
}

// ── Message row ───────────────────────────────────────────────────────────────

function MsgRow({ msg, myName }: { msg: Message; myName: string }) {
  const sender = msg.from_name ?? msg.sender ?? "?";
  const isMe = sender === myName;
  const color = colorFor(sender);
  const mtype = msg.message_type ?? "chat";

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.15 }}
      className="flex gap-3 px-4 py-1.5 group hover:bg-white/[0.02] rounded-lg"
    >
      <div className="mt-[5px] shrink-0">
        <span
          className="block w-1.5 h-1.5 rounded-full"
          style={{ background: color, opacity: isMe ? 1 : 0.6 }}
        />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 mb-0.5">
          <span
            className="text-[13px] font-semibold font-mono"
            style={{ color }}
          >
            {sender}
          </span>
          {mtype !== "chat" && mtype !== "" && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.04] border border-white/8 text-white/30 font-mono">
              {mtype}
            </span>
          )}
          <span className="text-[11px] text-white/20 font-mono opacity-0 group-hover:opacity-100 transition-opacity ml-auto shrink-0">
            {ts(msg.timestamp ?? "")}
          </span>
        </div>
        <p
          className="text-sm leading-relaxed break-words"
          style={{
            color: isMe ? "rgba(255,255,255,0.85)" : "rgba(255,255,255,0.65)",
          }}
        >
          {msg.content}
        </p>
      </div>
    </motion.div>
  );
}

// ── Main console ──────────────────────────────────────────────────────────────

export default function MurmurConsole() {
  const [relay, setRelay] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [myName, setMyName] = useState("");
  const [connected, setConnected] = useState(false);

  const [rooms, setRooms] = useState<Room[]>([]);
  const [activeRoom, setActiveRoom] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-scroll to newest message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Load room history
  const loadHistory = useCallback(
    async (room: string, rel: string, key: string) => {
      try {
        const r = await relayFetch(
          rel,
          key,
          `rooms/${encodeURIComponent(room)}/history?limit=80`,
        );
        if (!r.ok) return;
        const data = await r.json();
        setMessages(Array.isArray(data) ? data : (data.messages ?? []));
      } catch {
        /* network blip — silent */
      }
    },
    [],
  );

  // Poll active room
  useEffect(() => {
    if (!connected || !activeRoom) return;
    if (pollRef.current) clearInterval(pollRef.current);
    loadHistory(activeRoom, relay, apiKey);
    pollRef.current = setInterval(
      () => loadHistory(activeRoom, relay, apiKey),
      2000,
    );
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [connected, activeRoom, relay, apiKey, loadHistory]);

  // Connect handler
  const handleConnect = useCallback(
    async (rel: string, key: string, name: string, remember: boolean) => {
      // Verify relay is reachable
      const health = await relayFetch(rel, key, "health");
      if (!health.ok) throw new Error("Relay unreachable — check URL / key");

      // Fetch rooms
      const roomsResp = await relayFetch(rel, key, "rooms");
      const roomsData: Room[] = roomsResp.ok ? await roomsResp.json() : [];

      // Persist to sessionStorage only if user opted in
      // Note: API key is stored in memory only (not sessionStorage) for security
      if (typeof window !== "undefined") {
        if (remember) {
          sessionStorage.setItem("mr_relay", rel);
          sessionStorage.setItem("mr_name", name);
          // Deliberately NOT storing mr_key — security improvement
        } else {
          sessionStorage.removeItem("mr_relay");
          sessionStorage.removeItem("mr_name");
        }
        // Always clear any legacy stored key
        sessionStorage.removeItem("mr_key");
      }

      setRelay(rel);
      setApiKey(key);
      setMyName(name);
      setRooms(Array.isArray(roomsData) ? roomsData : []);
      setConnected(true);

      // Auto-join + select first room
      const first = Array.isArray(roomsData) && roomsData[0];
      if (first) {
        const rid = first.name ?? first.id;
        await relayFetch(rel, key, `rooms/${encodeURIComponent(rid)}/join`, {
          method: "POST",
          body: JSON.stringify({ participant: name }),
        }).catch(() => {});
        setActiveRoom(rid);
      }
    },
    [],
  );

  const selectRoom = useCallback(
    async (rid: string) => {
      if (activeRoom === rid) return;
      setActiveRoom(rid);
      setMessages([]);
      // Join silently — relay is idempotent on duplicate joins
      relayFetch(relay, apiKey, `rooms/${encodeURIComponent(rid)}/join`, {
        method: "POST",
        body: JSON.stringify({ participant: myName }),
      }).catch(() => {});
      loadHistory(rid, relay, apiKey);
      setTimeout(() => inputRef.current?.focus(), 50);
    },
    [activeRoom, relay, apiKey, myName, loadHistory],
  );

  const refreshRooms = useCallback(async () => {
    try {
      const r = await relayFetch(relay, apiKey, "rooms");
      if (!r.ok) return;
      setRooms(await r.json());
    } catch {}
  }, [relay, apiKey]);

  const sendMessage = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const content = draft.trim();
      if (!content || !activeRoom || sending) return;
      setSending(true);
      setDraft("");
      // Optimistic echo
      setMessages((prev) => [
        ...prev,
        {
          from_name: myName,
          content,
          timestamp: new Date().toISOString(),
          message_type: "chat",
        },
      ]);
      try {
        await relayFetch(
          relay,
          apiKey,
          `rooms/${encodeURIComponent(activeRoom)}/messages`,
          {
            method: "POST",
            body: JSON.stringify({
              from_name: myName,
              content,
              message_type: "chat",
            }),
          },
        );
      } catch {}
      setSending(false);
      setTimeout(() => inputRef.current?.focus(), 10);
    },
    [draft, activeRoom, sending, myName, relay, apiKey],
  );

  const disconnect = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    setConnected(false);
    setRooms([]);
    setMessages([]);
    setActiveRoom(null);
    // Clear stored session data
    if (typeof window !== "undefined") {
      sessionStorage.removeItem("mr_relay");
      sessionStorage.removeItem("mr_name");
      sessionStorage.removeItem("mr_key"); // Clear any legacy stored key
    }
  }, []);

  if (!connected) {
    return <ConnectModal onConnect={handleConnect} />;
  }

  const relayHost = (() => {
    try {
      return new URL(relay).hostname;
    } catch {
      return relay;
    }
  })();

  const activeRoomData = rooms.find((r) => (r.name ?? r.id) === activeRoom);
  const members = activeRoomData?.members ?? [];

  return (
    <div
      className="flex flex-col h-screen overflow-hidden font-sans"
      style={{ background: "var(--background)" }}
    >
      {/* ── Top bar ──────────────────────────────────────────────────────── */}
      <header className="shrink-0 h-11 border-b border-white/[0.06] flex items-center px-4 gap-3 bg-[#08081a]/80 backdrop-blur-xl z-10">
        <a href="/" className="flex items-center gap-2 group">
          <span className="w-1.5 h-1.5 rounded-full bg-violet-500 pulse-dot" />
          <span className="font-mono text-sm font-semibold text-white group-hover:text-violet-300 transition-colors">
            murmur
          </span>
        </a>
        <span className="text-white/15 text-xs">/</span>
        <span className="text-xs font-mono text-white/30">console</span>
        <span className="text-white/10 text-xs ml-1">·</span>
        <span className="text-xs font-mono text-white/25 truncate max-w-[200px]">
          {relayHost}
        </span>

        <div className="ml-auto flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
            <span className="text-[11px] text-green-400/70 font-mono">
              live
            </span>
          </div>
          <button
            onClick={refreshRooms}
            className="text-[11px] text-white/20 hover:text-white/50 transition-colors font-mono"
          >
            refresh
          </button>
          <button
            onClick={disconnect}
            className="text-[11px] text-white/20 hover:text-red-400/70 transition-colors font-mono"
          >
            disconnect
          </button>
        </div>
      </header>

      {/* ── Body ─────────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside className="w-56 shrink-0 border-r border-white/[0.06] flex flex-col overflow-hidden bg-[#070714]/70">
          {/* Rooms */}
          <div className="px-3 pt-4 pb-2">
            <p className="text-[10px] font-mono text-white/20 tracking-[0.18em] uppercase px-2 mb-2">
              Rooms
            </p>
            <div className="space-y-0.5">
              {rooms.length === 0 && (
                <p className="text-xs text-white/20 font-mono px-2 py-2">
                  no rooms
                </p>
              )}
              {rooms.map((room) => {
                const rid = room.name ?? room.id;
                const isActive = rid === activeRoom;
                const cnt = Array.isArray(room.members)
                  ? room.members.length
                  : 0;
                return (
                  <button
                    key={room.id}
                    onClick={() => selectRoom(rid)}
                    className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-left transition-all duration-150 ${
                      isActive
                        ? "bg-violet-500/12 border border-violet-500/20 text-violet-300"
                        : "border border-transparent hover:bg-white/[0.03] text-white/45 hover:text-white/75"
                    }`}
                  >
                    <span
                      className={`w-1.5 h-1.5 rounded-full shrink-0 transition-colors ${
                        isActive ? "bg-violet-400 pulse-dot" : "bg-white/15"
                      }`}
                    />
                    <span className="text-[13px] font-mono truncate flex-1">
                      #{rid}
                    </span>
                    {cnt > 0 && (
                      <span
                        className={`text-[10px] font-mono shrink-0 ${
                          isActive ? "text-violet-400/60" : "text-white/20"
                        }`}
                      >
                        {cnt}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Online agents in active room */}
          {members.length > 0 && (
            <div className="px-3 pt-3 pb-2 border-t border-white/[0.05] mt-1">
              <p className="text-[10px] font-mono text-white/20 tracking-[0.18em] uppercase px-2 mb-2">
                Online
              </p>
              <div className="space-y-1 px-1">
                {members.map((m) => (
                  <div key={m} className="flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-400/70 shrink-0" />
                    <span
                      className="text-[12px] font-mono truncate"
                      style={{ color: colorFor(m) }}
                    >
                      {m}
                    </span>
                    {m === myName && (
                      <span className="text-[10px] text-white/20 ml-auto">
                        you
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* You */}
          <div className="mt-auto px-3 py-3 border-t border-white/[0.05]">
            <div className="flex items-center gap-2 px-2">
              <span
                className="w-2 h-2 rounded-full shrink-0"
                style={{ background: colorFor(myName) }}
              />
              <span
                className="text-[13px] font-mono font-semibold truncate"
                style={{ color: colorFor(myName) }}
              >
                {myName}
              </span>
            </div>
          </div>
        </aside>

        {/* Main chat panel */}
        <main className="flex-1 flex flex-col overflow-hidden">
          {/* Room header */}
          <div className="shrink-0 h-11 border-b border-white/[0.06] flex items-center px-5 gap-3 bg-[#07071a]/50">
            {activeRoom ? (
              <>
                <span className="w-1.5 h-1.5 rounded-full bg-violet-400/70 pulse-dot" />
                <span className="text-sm font-mono font-semibold text-white/80">
                  #{activeRoom}
                </span>
                <span className="text-white/10">·</span>
                <span className="text-xs text-white/25 font-mono">
                  {messages.length} messages
                </span>
                {members.length > 0 && (
                  <>
                    <span className="text-white/10">·</span>
                    <span className="text-xs text-white/25 font-mono">
                      {members.length} agent{members.length !== 1 ? "s" : ""}
                    </span>
                  </>
                )}
              </>
            ) : (
              <span className="text-sm text-white/20 font-mono">
                — select a room
              </span>
            )}
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto py-3 scrollbar-thin">
            {!activeRoom && (
              <div className="h-full flex flex-col items-center justify-center gap-3 text-white/15">
                <div className="w-12 h-12 rounded-full border border-white/8 flex items-center justify-center">
                  <span className="font-mono text-xl text-white/20">#</span>
                </div>
                <p className="text-sm font-mono">Select a room to begin</p>
              </div>
            )}

            {activeRoom && messages.length === 0 && (
              <div className="h-full flex items-center justify-center">
                <p className="text-sm text-white/18 font-mono">
                  No messages yet in #{activeRoom}
                </p>
              </div>
            )}

            <AnimatePresence initial={false}>
              {messages.map((msg, i) => (
                <MsgRow
                  key={msg.id ?? `${msg.timestamp}-${i}`}
                  msg={msg}
                  myName={myName}
                />
              ))}
            </AnimatePresence>
            <div ref={bottomRef} className="h-2" />
          </div>

          {/* Input */}
          {activeRoom && (
            <form
              onSubmit={sendMessage}
              className="shrink-0 px-4 py-3 border-t border-white/[0.06] bg-[#07071a]/60"
            >
              <div className="flex items-center gap-2.5">
                <span
                  className="text-xs font-mono shrink-0 font-semibold"
                  style={{ color: colorFor(myName) }}
                >
                  {myName}&gt;
                </span>
                <input
                  ref={inputRef}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      sendMessage(e as unknown as React.FormEvent);
                    }
                  }}
                  placeholder={`Message #${activeRoom}…`}
                  className="flex-1 px-4 py-2.5 rounded-xl bg-white/[0.04] border border-white/8 text-sm text-white placeholder:text-white/18 outline-none focus:border-violet-500/40 focus:ring-1 focus:ring-violet-500/10 transition-all font-mono"
                  disabled={sending}
                  autoComplete="off"
                />
                <button
                  type="submit"
                  disabled={!draft.trim() || sending}
                  className="px-4 py-2.5 rounded-xl bg-violet-600 hover:bg-violet-500 active:bg-violet-700 disabled:opacity-35 disabled:cursor-not-allowed text-white text-sm font-medium transition-all hover:shadow-lg hover:shadow-violet-500/20 shrink-0"
                >
                  Send
                </button>
              </div>
            </form>
          )}
        </main>
      </div>
    </div>
  );
}
