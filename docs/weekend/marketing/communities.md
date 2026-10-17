# Communities and people worth reaching (draft)

> Status: **draft only** — a target list, not a contact log. Nothing here has
> been sent, DMed, emailed or posted. Outreach only happens if Arav reads this
> and says go.
>
> Rules for whoever works this list: one tailored post or message per target,
> never a blast; lead with the demo, not the pitch; if someone says no, stop.
> **Verify every handle before using it** — handles and subreddit rules rot,
> and a wrong @ is worse than no @.

---

## Where to post (ordered by fit, not reach)

| Community | Where | Why it fits |
| --- | --- | --- |
| Hacker News | `Show HN` | The audience already runs Claude Code/Codex side by side and argues about exactly this. The honest "it's 0.5.0 and early" framing lands better here than anywhere. |
| r/LocalLLaMA | Reddit | Cares about local models and Ollama; our opencode route (`--model ollama/qwen3:4b`) is the hook. Post the demo, not the launch copy. |
| r/ChatGPTCoding | Reddit | The "I have three agent terminals open" pain is the daily topic there. |
| r/ClaudeAI , r/ClaudeCode | Reddit | Claude Code is our most-used harness; these are where its users ask "how do I run more than one". |
| Lobsters | lobste.rs | Small, high-signal, allergic to hype. Only post if the post is genuinely technical (the relay/locks design, not the launch). |
| opencode Discord / GitHub Discussions | Discord + GitHub | Free, any-model harness; "run a free DeepSeek agent that talks to Claude" is a concrete, self-contained demo for them. |
| MCP community | `modelcontextprotocol` Discussions / Discord | We ship an MCP server; the audience is building MCP clients and servers and will actually try it. |
| Gemini CLI + Codex CLI discussions | GitHub | Their users already script them; our `agent add` is the missing coordination layer. |
| Ollama Discord | Discord | Local-model users who want a second local agent reviewing the first. |
| Product Hunt | producthunt.com | One-shot visibility; only after the install path is boring-clean on a fresh machine. |
| dev.to / Hashnode | dev.to | Long-form "how I run a two-model review loop on my own repos" article repurposed from the demo script. |

## People and teams (by their public work, not by guesswork)

Ranked by how likely they are to care *and* to say something useful. These are
public projects and organisations, not private individuals — the point is to
reach people who already work on this problem, not to farm mentions.

| Who (public) | Why them | The angle that isn't a pitch |
| --- | --- | --- |
| Maintainers of adjacent agent runners — **claude-squad** (`smtg-ai/claude-squad`), **Vibe Kanban** (`BloopAI/vibe-kanban`), **codeg** (`spacering-net/codeg`), **Claude-Code-Workflow** (`catlog22/Claude-Code-Workflow`), **claude_codex_bridge** (`SeemSeam/claude_codex_bridge`) | They solved neighbouring halves: worktrees per task, review-before-apply, cross-CLI bridging. They know the exact pain. | "You do worktrees/review; we do the shared room and cross-vendor handoff. Want to compare notes on where the coordination layer should sit?" |
| People building MCP servers/clients | They ship the transport our tools ride on; a real multi-agent workload is a good stress test for it. | "Here's a workload that hammers MCP with concurrent agents — happy to share what broke." |
| Agent-framework maintainers (CrewAI, AutoGen, LangGraph, OpenAgents) | They have orchestration; they don't have a shared substrate across *different vendors' CLIs*. | "Your framework inside one vendor's runtime vs. agents from four vendors in one room — different problem, maybe complementary." |
| Writers who cover agent tooling (e.g. Simon Willison, and the newsletters that picked up Cursor/Claude Code) | They test tools on camera and post the honest result; a 60-second real demo is what they actually want. | Send the demo clip plus the caveat, not a press kit. |
| YC / accelerator partners working on dev tools | Fit, and the story is concrete. | Only when there's real usage to talk about; a launch-week cold pitch is noise. |

> The five repos above are the ones whose READMEs we actually read this weekend
> (`docs/weekend/research/`); their URLs were checked link-by-link. Anyone added
> later must be verified the same way before they go on the list.

## Do-not-do list

- No mass @-mentions, no DM templates, no "hey, check out my project" to
  strangers.
- No posting in a community whose rules forbid self-promo; read the rules first.
- No paid promotion, no engagement bait, no fake "we hit 1k users" style posts.
- No contacting anyone attached to a project we're comparing against in a way
  that reads as a dunk. Cite their work, thank them, move on.
