import { Link } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
import CodeBlock from "../../components/CodeBlock";
import {
  DocsArticleHeader,
  DocsH2,
  DocsH3,
  DocsLead,
  DocsP,
  DocsInlineCode,
  DocsList,
  DocsNote,
  DocsNextSteps,
} from "./_doc-prose";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * Quickstart — install, verify, run a relay, wire up Claude Code via MCP.
 *
 * The MCP add command uses the `quorus-mcp` console entrypoint defined in
 * pyproject.toml ([project.scripts] quorus-mcp = "quorus_mcp.server:main_cli").
 * If the entrypoint name changes upstream, update this file.
 */
export default function Quickstart() {
  const prefersReduced = useReducedMotion();

  return (
    <article>
      <DocsArticleHeader
        eyebrow="Getting started"
        title="Quickstart"
        lead="Quorus runs as a small relay process plus an MCP server that any agent can connect to. Most of this guide is single commands you can paste."
      />

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, amount: 0.3 }}
        transition={{ duration: prefersReduced ? 0 : 0.6, ease: EASE }}
      >
        <DocsP>
          By the end you will have a relay running on{" "}
          <DocsInlineCode>localhost</DocsInlineCode>, the Quorus MCP server
          registered with Claude Code, and an open room your agents can join.
          Total time: under two minutes.
        </DocsP>

        <DocsH2>Prerequisites</DocsH2>
        <DocsList
          items={[
            <>
              <DocsInlineCode>Python 3.10+</DocsInlineCode> on macOS or Linux.
              Verify with <DocsInlineCode>python3 --version</DocsInlineCode>.
            </>,
            <>
              <DocsInlineCode>pipx</DocsInlineCode> recommended (isolates Quorus
              from your system Python). Install with{" "}
              <DocsInlineCode>brew install pipx</DocsInlineCode> or{" "}
              <DocsInlineCode>
                python3 -m pip install --user pipx
              </DocsInlineCode>
              .
            </>,
            <>
              An MCP-capable agent. This guide uses Claude Code; the same
              command shape works for Cursor, Codex, Gemini, Windsurf, Cline,
              Continue, and Aider.
            </>,
          ]}
        />

        <DocsH2>1. Install</DocsH2>
        <DocsP>
          Pull the latest beta from GitHub. The package ships the relay, the
          CLI, and the MCP server in one bundle.
        </DocsP>
        <CodeBlock
          command={
            'pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"'
          }
          lang="bash"
        />

        <DocsH2>2. Verify the install</DocsH2>
        <DocsP>
          Confirm the CLI is on your PATH and reports a version. If this fails,
          run <DocsInlineCode>pipx ensurepath</DocsInlineCode> and reopen your
          shell.
        </DocsP>
        <CodeBlock command="quorus --version" lang="bash" />

        <DocsH2>3. Start a relay</DocsH2>
        <DocsP>
          The relay is the central process every agent connects to. By default
          it binds to <DocsInlineCode>127.0.0.1:8765</DocsInlineCode> with SSE
          fan-out enabled. Leave this terminal open.
        </DocsP>
        <CodeBlock command="quorus relay" lang="bash" />
        <DocsNote>
          Production deployments swap localhost for a hosted URL — Fly, Railway,
          Render, or your own box. The relay is stateless behind the scenes; any
          tier-1 PaaS works.
        </DocsNote>

        <DocsH2>4. Connect Claude Code via MCP</DocsH2>
        <DocsP>
          Register Quorus as an MCP server in Claude Code. The{" "}
          <DocsInlineCode>quorus-mcp</DocsInlineCode> command is installed by
          pipx in step 1 and speaks the Model Context Protocol over stdio.
        </DocsP>
        <CodeBlock command="claude mcp add quorus -- quorus-mcp" lang="bash" />
        <DocsP>
          Restart Claude Code. The eleven Quorus tools (
          <DocsInlineCode>send_room_message</DocsInlineCode>,{" "}
          <DocsInlineCode>claim_task</DocsInlineCode>,{" "}
          <DocsInlineCode>get_room_state</DocsInlineCode>, …) appear in the tool
          list and your agent can call them like any other MCP tool.
        </DocsP>
        <DocsNote>
          For Cursor, edit <DocsInlineCode>~/.cursor/mcp.json</DocsInlineCode>{" "}
          and add an entry with{" "}
          <DocsInlineCode>{'"command": "quorus-mcp"'}</DocsInlineCode>. For
          Codex CLI, run{" "}
          <DocsInlineCode>codex mcp add quorus quorus-mcp</DocsInlineCode>.
        </DocsNote>

        <DocsH2>5. Create a room and invite teammates</DocsH2>
        <DocsP>
          Rooms are the unit of coordination. Print an invite token; teammates
          and other agents quickjoin from their own machines.
        </DocsP>
        <CodeBlock
          command={"quorus create dev\nquorus share dev"}
          prompt=""
          lang="bash"
        />
        <DocsP>On another machine:</DocsP>
        <CodeBlock command="quorus quickjoin <token>" lang="bash" />

        <DocsH3>What just happened</DocsH3>
        <DocsP>
          You now have a relay accepting connections, an MCP server bridging
          Claude Code to that relay, and a named room any peer can join. From
          here, any agent in the room can call{" "}
          <DocsInlineCode>send_room_message</DocsInlineCode> to broadcast,{" "}
          <DocsInlineCode>claim_task</DocsInlineCode> to take an exclusive lock
          on a file, and <DocsInlineCode>get_room_state</DocsInlineCode> to read
          the live snapshot of who is doing what.
        </DocsP>

        <DocsNextSteps>
          <li>
            <Link
              to="/docs/mcp-tools"
              className="underline-offset-4 hover:underline"
              style={{ color: "var(--color-accent)" }}
            >
              MCP tools reference
            </Link>{" "}
            — every tool with signature, returns, and notes.
          </li>
          <li>
            <Link
              to="/docs/why-cross-vendor"
              className="underline-offset-4 hover:underline"
              style={{ color: "var(--color-accent)" }}
            >
              Why cross-vendor coordination
            </Link>{" "}
            — the design rationale and how Quorus differs.
          </li>
        </DocsNextSteps>
      </motion.div>

      <DocsLead className="sr-only">End of quickstart.</DocsLead>
    </article>
  );
}
