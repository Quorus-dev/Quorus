# Stall Talk Track — Quorus Builders' Showcase

Single page, two sections. Read once before the demo. Don't read it AT the demo.

---

## A. The 60-second pitch (5 beats x ~12s)

Visitor walks up. Don't introduce yourself. Don't say "let me show you something cool." Just open with the problem.

**Beat 1 — Open (the problem) (~12s)**

"Most coding agents work in isolation. Claude Code on your machine, Codex in your terminal, Cursor in its IDE. None of them know what the others are doing. So you end up coordinating them by hand, copy-pasting context between four windows."

**Beat 2 — Pivot (the move) (~12s)**

(Turn the laptop so they can see the TUI. 4 agents listed in the sidebar.)

"This is one chat room with four coding agents in it. Your machine, your existing logins. No new API keys. They each see what the others post."

**Beat 3 — Live demo (~12s)**

"Type any @-mention. Whatever you want."

(Hand them the keyboard. If they freeze, suggest: "try `@arav-claude what's 7 times 8` or `@arav-codex pick a number between 1 and 100`.")

(While the reply streams in: "That's running on my laptop right now — Claude Code's headless CLI under the hood. Same login I use locally. The relay is just routing the @-mention to the right agent.")

**Beat 4 — Moat (~12s)**

"What's interesting is the wire format. Claim, disagree, defer, queue, vote, interrupt. We published it Apache-2.0. Other agent frameworks can adopt the same verbs and rooms become interoperable. The TUI is one client. The protocol is the moat."

**Beat 5 — Close (~12s)**

"Not released yet. Shipping soon. If you want early access, drop your email here."

(Point at the QR code. Don't push. If they're already typing on the laptop, let them keep playing.)

---

## B. Most-likely visitor questions (1-line answers)

Q: "Wait, how does this differ from MCP?"
A: "MCP is one agent talking to one toolset. Quorus is many agents in one room talking to each other and to humans. We use MCP underneath for the relay-to-agent leg."

Q: "Is this open source?"
A: "Yes. github.com/Quorus-dev/Quorus, Apache-2.0 on the wire-format spec, MIT-style on the relay. Not on PyPI yet but pip-install from the repo works today."

Q: "How does it work without API keys?"
A: "Each agent uses its harness's own OAuth. Claude Code's login, Codex's login, Gemini's login. The relay never sees a provider credential — it just routes messages between named identities."

Q: "What's the moat?"
A: "The wire format. If OpenAgents and CrewAI adopt the social verbs, we become the HTTP for agents. We're cold-emailing maintainers this month."

Q: "Can I get early access?"
A: "Yeah, drop your email here." (Point at QR.)

---

## Failure modes (what to do if it breaks)

- **Reply is slow (>20s)**: "First call is cold-start, model's loading. Try a follow-up — second one is fast." Then keep talking through Beat 4.
- **Daemon dropped**: open another terminal tab, run `bash scripts/stall_demo_local.sh status`. If a daemon is dead, `bash scripts/stall_demo_local.sh reset` mints fresh state in ~25s.
- **Visitor's @-mention has no reply**: either the agent name is wrong (they typed `@claude` not `@arav-claude`), or the harness exited mid-call. Hand them the QR and pivot to the waitlist.
- **Whole laptop hangs**: laugh, say "this is why we're not released yet," hand them the QR.
