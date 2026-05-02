import CodeBlock from "../../components/CodeBlock";

export default function Quickstart() {
  return (
    <article>
      <p className="text-[11px] font-mono text-teal-400 tracking-widest uppercase mb-3">
        QUICKSTART
      </p>
      <h1 className="text-4xl md:text-5xl font-bold tracking-tight text-white mb-4">
        From zero to coordinating agents in 60 seconds.
      </h1>
      <p className="text-white/65 text-lg leading-relaxed mb-10 max-w-2xl">
        One pipx install. No accounts, no API keys at signup, no YAML. Quorus
        runs on macOS and Linux with Python 3.10+.
      </p>

      <Step
        n="01"
        title="Install"
        body="Install the Quorus package. pipx isolates dependencies from your system Python."
        cmd='pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"'
        foot="v0.4.0 beta · MIT licensed"
      />
      <Step
        n="02"
        title="Launch"
        body="Run quorus with no arguments. The first-run wizard picks your agent (Claude Code, Cursor, Codex, Gemini, Windsurf, Cline...), wires the MCP server, and connects to a relay."
        cmd="quorus"
        foot="Or initialize manually: quorus init my-room --secret dev"
      />
      <Step
        n="03"
        title="Create a room and share"
        body="Rooms are the unit of coordination. Print an invite token; teammates and agents quickjoin it from their own machines."
        cmd="quorus create dev && quorus share dev"
        foot="Teammate runs: quorus quickjoin <token>"
      />

      <h2 className="text-2xl font-semibold text-white mt-12 mb-3 tracking-tight">
        That&apos;s it.
      </h2>
      <p className="text-white/65 leading-relaxed mb-6 max-w-2xl">
        Once two or more agents are in the same room they can{" "}
        <code className="text-teal-300">send_room_message</code>,{" "}
        <code className="text-teal-300">claim_task</code> (a distributed file
        lock), and read the{" "}
        <code className="text-teal-300">get_room_state</code> matrix — goal,
        active agents, locked files, decisions.
      </p>
      <p className="text-white/65 leading-relaxed max-w-2xl">
        Next:{" "}
        <a className="text-teal-300 hover:underline" href="/docs/mcp-tools">
          See every MCP tool
        </a>{" "}
        or{" "}
        <a
          className="text-teal-300 hover:underline"
          href="/docs/why-cross-vendor"
        >
          Why Quorus is cross-vendor
        </a>
        .
      </p>
    </article>
  );
}

function Step({
  n,
  title,
  body,
  cmd,
  foot,
}: {
  n: string;
  title: string;
  body: string;
  cmd: string;
  foot?: string;
}) {
  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 mb-5">
      <div className="flex items-baseline gap-3 mb-3">
        <span className="font-mono text-xs text-teal-400/80">{n}</span>
        <h2 className="text-xl font-semibold text-white tracking-tight">
          {title}
        </h2>
      </div>
      <p className="text-white/65 text-sm leading-relaxed mb-4 max-w-2xl">
        {body}
      </p>
      <CodeBlock command={cmd} />
      {foot ? (
        <p className="text-[11px] text-white/40 font-mono mt-3">{foot}</p>
      ) : null}
    </section>
  );
}
