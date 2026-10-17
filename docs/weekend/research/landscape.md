# Quorus: Competitive Landscape and Differentiator Strategy

Date: 2026-10-17. Author: product-engineer agent. No repo modified.

**Evidence labels.**
- **[V] verified**: I read it on a primary source (official docs, the repo page, the paper) during this session.
- **[CR] company-reported**: the company or creator said it, usually relayed by press. Funding, ARR and user counts are all CR.
- **[A] assumption**: my own inference, or a number from a secondary aggregator that I could not check against a primary source. Most star counts fall here.

Search snippets were the main research tool. Treat each URL as where the claim came from, not as a full audit.

---

## 1. The uncomfortable headline

**"Group chat for your coding agents" is no longer a novel category. On its own I score it 4/10.** The idea of a control tower for many vendors' agents has been taken by four kinds of players:

- **Platforms**: GitHub Agent HQ, Devin Desktop.
- **Labs' own apps**: the Codex app, Claude Code agent teams.
- **Funded startups**: Conductor, Factory.
- **Viral open source**: Paperclip, codeg, Gas Town, Vibe Kanban, claude-squad, Agent Mail, Oryxa.

Several of them already cover pieces of Quorus's feature list:

- **@-mention handoffs between vendors**: codeg.
- **Many humans and many agents in one live room**: Oryxa.
- **A lead/org chart with budgets and heartbeat wakeups**: Paperclip, which has 103k stars.

**What nobody has is a learning layer.** No one measures which vendor's agent actually produces work that survives in this repo, and no one feeds that back into who gets the next task, how quota gets spent, and how the team's instructions improve. Quorus is unusually placed to build it, for three reasons:

1. It already sees every vendor's transcript, every cross-vendor review, every commit, every quota event, and every handoff in one local log.
2. Its agents belong to different people on different machines and subscriptions, which no single-vendor tool can see.
3. It is local and MIT, so the outcome data stays with the repo owner.

**The strategy in one line: stop competing on orchestration, which is crowded, and compete on measured outcomes.**

---

## 2. Landscape

### 2.1 Commercial agents and control surfaces

| Player | What it does | Traction / pricing | Gap Quorus can own |
|---|---|---|---|
| **Cognition / Devin** | Autonomous SWE agent. In July 2025 it bought Windsurf, with $82M ARR ([TechCrunch](https://www.techcrunch.com/2025/07/14/cognition-maker-of-the-ai-coding-agent-devin-acquires-windsurf/), [Sacra](https://sacra.com/research/cognition) [CR]). On 2026-06-02 Windsurf was rebranded **Devin Desktop**, an "Agent Command Center" that runs any ACP agent (Codex, Claude Agent, OpenCode) next to Devin ([Cognition blog](https://cognition.ai/blog/introducing-devin-desktop) [V]). Windsurf "Arena Mode" runs blind side-by-side model runs that humans vote on ([Devin blog Wave 14](https://devin.ai/blog/windsurf-wave-14), [InfoQ](https://infoq.com/news/2026/02/windsurf-arena-mode/) [V]). | Crossed $1B run-rate on 2026-09-25; $48B valuation ([runtimewire](https://runtimewire.com/article/cognition-billion-annualized-revenue-scott-wu), [SaaSRise](https://www.saasrise.com/news/cognition-hits-1-b-annualized-revenue-run-rate-four-months-after-492-m-milestone-b3061da1-6103-4735-8ec3-04c66a1a30b4) [CR]). Pricing: Free / Pro $20 / Max $200 / Teams $80 + $40 per seat ([automationatlas](https://automationatlas.io/answers/devin-pricing-explained-2026/) [A]). | Arena picks winners by **human votes** and builds a **global** leaderboard. Nobody ranks agents by **outcomes in your repo**: merged vs reverted, review verdicts, test pass. Devin Desktop has one owner per seat, so it cannot put agents owned by different people in one room. |
| **Factory (Droids)** | Enterprise agent suite. When the plan limit runs out, "Droid Core" falls back to free open-weight models ([usagepricing](https://usagepricing.com/blueprint/factory) [A]). | Series C of $150M at $1.5B in April 2026, then $200M at $5B in September 2026 ([SVIC](https://siliconvalleyinvestclub.com/2026/09/17/factory-raises-200-million-at-a-5-billion-valuation/), [cityam](https://www.cityam.com/?p=2447558) [CR]). Pro $20 / Plus $100 / Max $200 [A]. | The fallback is one vendor's own pool. Factory does not schedule work across the **separate subscriptions you already pay for**. |
| **Cursor** | Background agents in cloud VMs. Parallel agents (up to 8, each in its own worktree). `/best-of-n` runs one task on several models, and a parent agent comments on the results ([morphllm](https://www.morphllm.com/cursor-background-agents), [Cursor forum](https://forum.cursor.com/t/cursor-3-worktrees-best-of-n/156507) [A]). Bugbot reviews PRs. | Over $4B run-rate by June 2026 ([dealroom](https://dealroom.co/news/134107-cursor-tops-4b-annualized-revenue/) [CR]). Acquired by SpaceX for $60B ([techzine](https://www.techzine.eu/news/devops/142197/spacex-acquires-cursor-for-60-billion/) [CR]). | Best-of-n is a one-shot human choice. **Nothing is learned from it.** It only works with Cursor-hosted models, in one user's editor. |
| **OpenAI Codex (cloud + app)** | `codex cloud exec --attempts 1-4` runs best-of-N with the **same model** ([Vaughan](https://codex.danielvaughan.com/2026/04/01/codex-cloud-exec-best-of-n-attempts/) [A]). The Codex app (macOS on 2026-02-02, Windows on 2026-03-04) is a "command center" with built-in worktrees ([heise](https://heise.de/-11165126), [IntuitionLabs](https://intuitionlabs.ai/articles/openai-codex-app-ai-coding-agents) [CR]). | Bundled with ChatGPT plans [CR]. | Single vendor. Quorus has already watched Codex Plus limits run out within hours, which is evidence that capacity across vendors needs managing. |
| **Google Jules** | Async cloud agent. GA at I/O on 2026-05-19. Free: 15 tasks/day. AI Pro ($19.99): 100/day. Ultra ($124.99): 300/day ([pillitteri](https://pasqualepillitteri.it/en/news/2163/google-jules-async-ai-coding-agent) [A]). | Bundled with Google AI plans [CR]. | Its quota is task-based, which is easy to model. That makes it a good candidate for a cross-vendor budget scheduler. |
| **GitHub Copilot coding agent / Agent HQ** | Announced at Universe on 2025-10-28. "Mission Control" assigns and tracks agents from Anthropic, OpenAI, Google, Cognition and xAI ([GitHub blog](https://github.blog/news-insights/company-news/welcome-home-agents/) [V]). You can assign several agents to one task and compare them ([iTWire](https://itwire.com/business-it-news/open-source/github-announces-agent-hq-the-control-tower-for-every-coding-agent) [CR]). Moved to usage-based "AI Credits" on 2026-06-01 ([directionsonmicrosoft](https://www.directionsonmicrosoft.com/github-copilot-to-move-to-usage-based-pricing-in-june) [A]). | Largest distribution in dev tools [A]. | **This is the biggest threat**: GitHub is the neutral multi-vendor layer. But it is cloud-only, billed through Copilot, and the comparison is manual. It does not run agents on **your local subscriptions and machines**, and it does not learn routing. |
| **Amp** | Spun out of Sourcegraph in late 2025. Routes by mode: Opus for "smart", GPT-5.5 for "deep" and the Oracle, Gemini for review and search. Orbs (remote environments) since July 2026. Puck, a meta-agent, since 2026-07-20 ([rywalker](https://rywalker.com/research/amp-sourcegraph), [tessl](https://tessl.io/blog/sourcegraph-spins-out-ai-coding-agent-amp-as-a-standalone-company/) [A]). | $10/day free allowance; pay-as-you-go at zero markup [A]. | Its routing is **hand-set per mode**. Nobody learns it from repo outcomes. This is the clearest evidence that routing helps and that nobody adapts it per repo. |
| **Claude Code** | Subagents report only to their parent. **Agent teams** (experimental, v2.1.32+) add a lead, a shared task list, a mailbox, and teammates that message each other ([docs](https://code.claude.com/docs/en/agent-teams) [V]). Remote Control lets you drive a local session from phone or web, end to end encrypted ([Help Net Security](https://www.helpnetsecurity.com/?p=358918) [CR]). Auto-continue resumes after a limit resets, but won't wait for resets more than 24h out ([wmedia](https://wmedia.es/en/tips/claude-code-session-limit-auto-continue) [A]). | — | Agent teams is **Quorus's architecture, but for Claude only and one person only**. Quorus must not be "agent teams with more vendors". It has to be the layer that *measures* teams. |

### 2.2 Open-source orchestrators (the crowded middle)

| Project | What it does | Signal | Gap |
|---|---|---|---|
| **Paperclip** | A "zero-human company": org chart, budgets per company, agent and project (auto-pause at the cap), heartbeat wakeups, many orgs, many users with RBAC. Runs Claude Code, Codex, Cursor, Gemini, OpenClaw and webhook agents ([repo](https://github.com/paperclipai/paperclip) [V]). | **103.3k stars, 17.3k forks** [V]. Launched 2026-03-02 ([fast.io](https://fast.io/resources/paperclip-ai-review-2026/) [A]). | **Closest to Quorus's "agent company" vision, with a far bigger following.** It has **no learning or optimization loop** [V, from the README summary]. Budgets cap spend; they do not *optimize* it. |
| **codeg** | One workspace for 12+ agent CLIs: @-delegation between agents, queued tasks in worktrees, sessions handed between agents, plus mobile, Telegram and Lark ([brightcoding](https://blog.brightcoding.dev/2026/08/01/xintaofeicodeg-unify-claude-code-codex-agents-in-one-workspace) [A]). | Trending [A]. | Already covers @-handoff across vendors. Built for one owner. |
| **Oryxa** | "Many people and many agents in one room, live." Append-only event log, smart message routing, ACP transport. Apache-2.0, v0.6 published 2026-08-20 ([pkg.go.dev](https://pkg.go.dev/github.com/Oryxa-Vault/oryxa) [V]). | Pilot stage [V]. | **Directly overlaps with multi-person rooms.** It has no worktrees, quota management or outcome loop. |
| **Gas Town** (Steve Yegge) | Orchestrates 20-30 agents through tmux, a Mayor role and a Beads ledger. Released 2026-01-01 ([heise](https://www.heise.de/en/background/Full-Control-Gas-Town-Orchestrates-Ten-or-More-Coding-Agents-11178824.html?seite=2) [CR]). | Strong mindshare [A]. | Built for one power user. Its roles are fixed, not learned. |
| **Vibe Kanban** | Kanban board for parallel agents. About 28k stars. **Its company shut down on 2026-04-10**; the project is now community-run ([sourcepulse](https://www.sourcepulse.org/projects/10592239), [alternativeto](https://alternativeto.net/software/vibe-kanban/about) [A]). | — | **Warning: a good open-source orchestrator with no money loop died.** |
| **claude-squad** | tmux plus worktrees for many CLI agents. AGPL, about 8.4k stars ([starlog](https://starlog.is/articles/ai-dev-tools/smtg-ai-claude-squad) [A]). | — | Just a session manager. |
| **Conductor** (Melty Labs, YC S24) | Mac app that runs Claude Code and Codex in worktrees, with Linear integration. About $24.5M raised ([morphllm](https://www.morphllm.com/conductor-ai-coding) [A]). | Free [A]. | One user, one Mac. |
| **Agent Mail** | MCP server giving agents identities, inboxes and advisory file leases, with history in Git and SQLite ([repo](https://github.com/Dicklesworthstone/mcp_agent_mail) [V]). | Over 1.7k stars for the Rust port [A]. | Only a messaging layer. |
| **Claude-Code-Workflow** | JSON-driven multi-CLI workflows (Gemini, Qwen, Codex). About 2.1k stars ([gittrend](https://gittrend.io/repo/catlog22/Claude-Code-Workflow) [A]). | — | Workflows are static. |
| **claude-codex-bridge** | MCP bridge that lets Claude start, steer and resume Codex tasks. 5 stars ([glama](https://glama.ai/mcp/servers/bwas95yfws) [A]). | — | Shows the demand is real; adoption is trivial. |
| **PAL MCP** (formerly Zen) | Claude asks Gemini, GPT and others for second opinions and multi-model "consensus" debates ([mcpservers](https://mcpservers.org/ja/servers/BeehiveInnovations/zen-mcp-server) [A]). | Popular [A]. | Cross-model review used as a tool call. Nothing is learned or recorded as outcomes. |
| **CrewAI / LangGraph / AutoGen / MetaGPT / ChatDev** | Frameworks for building agent apps. AutoGen is in maintenance mode; Microsoft Agent Framework (RC, Feb 2026) replaces it. LangGraph 1.0 shipped Oct 2025 ([fast.io](https://fast.io/resources/langgraph-vs-autogen/) [A]). CrewAI: $18M raised, $3.2M ARR in 2025, about 53k stars ([rywalker](https://rywalker.com/research/crewai) [CR/A]). MetaGPT about 70k stars, ChatDev about 33.5k ([aiwiki](https://aiwiki.ai/wiki/MetaGPT), [gittrend](https://gittrend.io/repo/OpenBMB/ChatDev) [A]). | — | These are a different market: SDKs for *building* agents, not coordinating ready-made coding CLIs. ChatDev and MetaGPT simulate roles in one process with one model, not real vendors. |
| **opencode** | Agent CLI that works with any provider; Quorus's way into "any model". About 175k+ stars ([star-history](https://star-history.com/anomalyco/opencode) [A]). | — | A substrate Quorus builds on, not a competitor. |

### 2.3 Optimization and self-improvement loops (the ideas to borrow)

| System | Mechanic | Result | What Quorus can take |
|---|---|---|---|
| **AlphaEvolve** (DeepMind) | Gemini ensemble plus automated evaluators plus evolutionary selection | Better datacenter scheduling; some kernels sped up by about a third ([MIT TR](https://www.technologyreview.com/2025/05/14/1116438/google-deepminds-new-ai-uses-large-language-models-to-crack-real-world-problems/amp/) [CR]). Managed service went GA 2026-07-09 [A]. | **Diverse generators plus a hard evaluator beat any single model.** Quorus's cross-vendor rooms *are* a diverse-generator ensemble. |
| **OpenEvolve** | Open-source AlphaEvolve; works with any OpenAI-compatible model | About 6.4k stars ([oosmetrics](https://oosmetrics.com/repo/algorithmicsuperintelligence/openevolve) [A]) | The evaluator-gated "keep if better" loop. |
| **GEPA** (in DSPy) | Reflects on full natural-language traces and keeps a Pareto set of candidate prompts | +20% over GRPO with up to 35x fewer rollouts. ICLR 2026 oral ([arXiv 2507.19457](https://arxiv.org/pdf/2507.19457v2) [V]) | **Transcripts are the training signal.** Quorus has multi-vendor transcripts that no lab has. |
| **Darwin Gödel Machine** | Agent rewrites its own scaffold and is scored on SWE-bench | 20% to 50% on SWE-bench. Also **hacked its reward and faked logs** ([Sakana](https://sakana.ai/dgm/) [V], [The Register](https://www.theregister.com/2025/06/02/self_improving_ai_cheat/) [CR]) | Self-improvement needs a held-out, tamper-proof evaluator. |
| **Karpathy autoresearch** | Edit, run a fixed 5-minute experiment, keep only if better, about 100 runs overnight | 21k+ stars within days of 2026-03-07 ([The Neuron](https://www.theneuron.ai/explainer-articles/andrej-karpathys-autoresearch-tiny-repo-big-implications/) [A]) | A room "metric mode". |
| **SkillOpt-Sleep** (Microsoft) | Mines past sessions, replays recurring tasks, and adopts CLAUDE.md or SKILL.md edits only if they pass a **held-out gate** ([tessl](https://tessl.io/registry/skills/github/microsoft/SkillOpt/skillopt-sleep) [A]). claude-reflect does a lighter version ([skillselion](https://skillselion.com/plugin/BayramAnnakov/claude-reflect) [A]). | — | **Prior art for the retro loop, at single-agent scope.** |
| **Agent-as-a-Router** (arXiv 2606.22902) | Router uses per-dimension, per-model performance statistics | +15.3% relative from task-dimension stats alone ([HF](https://huggingface.co/papers/2606.22902.md) [V]) | **Academic evidence that outcome-based routing pays off.** Not a product, and not tuned per repo. |
| **Scrouting** (arXiv 2608.04804) | A 7B scout explores the repo and hands off to one of four fixers | Matches the best single model at about 1/5 the cost per resolved task. **The structured handoff, not the routing, drives the gain** ([arXiv](https://arxiv.org/abs/2608.04804) [V]) | Handoff quality matters more than the router. |
| **Self-preference bias** | LLM judges rate their own model and model family higher ([arXiv 2508.06709](https://arxiv.org/abs/2508.06709v1) [V]) | — | **Judges must come from a different model family than the candidate.** Quorus's multi-vendor setup makes that possible; single-vendor tools cannot. |
| **Meta ACH** | Mutation-guided LLM test generation; engineers accepted 73% of its tests ([arXiv 2501.12862](https://arxiv.org/abs/2501.12862) [V]) | — | Tests the reviewer can't defeat with a mutant are the right signal for "weak tests". |
| **git-ai** | Ties each AI-written line to agent, model and transcript via git notes ([usegitai](https://usegitai.com/agent-blame) [A]) | — | Attribution exists. **Scoring outcomes on top of attribution does not.** |
| **CodexBar** | Menu-bar app showing quotas and reset countdowns for 69 providers, with pace tracking ([repo](https://github.com/steipete/CodexBar) [A]) | — | Shows quota **but does not schedule work** against it. |

---

## 3. Differentiator ideas

Scoring:
- **Novelty** is scored against the prior art above. 8 or higher means I found nothing doing it.
- **Effort**: S is 1 day or less, M is 2-4 days, L is more than a week, each with agents doing the work.

### 1. Outcome Ledger + Repo Scorecard
- **Mechanic:** Every task, handoff and commit is tagged with (agent, vendor, model, owner, task type, tokens and turns, quota cost). The ledger then tracks what happened next:
  - tests passed or failed
  - cross-vendor review verdict
  - human edits within 48h
  - reverted, or still present at 7 and 14 days
  `quorus scorecard` prints per-vendor success and cost by task type for *this repo*.
- **Pain:** Arav ran 6 agents across 4 providers for 10 hours and cannot say which one is worth its subscription.
- **Prior art:** git-ai ([link](https://usegitai.com/agent-blame)) does attribution without outcomes. Windsurf Arena ([link](https://devin.ai/blog/windsurf-wave-14)) uses human votes and a global board.
- **Novelty 6.** Attribution is solved. Joining it to outcomes across vendors and owners is new, but simple.
- **Effort:** S-M.
- **Proof:** Scorecard numbers for a weekend room. The proof that matters is that one routing or subscription decision changes because of it.
- **Fails if:** (a) few samples give noisy numbers; (b) confounding: the lead gives hard tasks to Claude, so Claude looks worse; (c) squash merges and rebases lose attribution.

### 2. Outcome-learned routing (repo-local bandit)
- **Mechanic:** The room lead assigns work by Thompson sampling. The arms are (task type × agent). Rewards come from the ledger, and quota cost is weighted by how much capacity each agent has left.
- **Pain:** Today a human or the lead guesses who should do what.
- **Prior art:**
  - Agent-as-a-Router ([HF](https://huggingface.co/papers/2606.22902.md)) runs on benchmarks, not live repos.
  - Amp ([link](https://rywalker.com/research/amp-sourcegraph)) routes by hand-set modes.
  - claude-code-router ([link](https://www.morphllm.com/claude-code-router)) uses static rules and is end of life.
- **Novelty 7.** Shipping routing learned from merged and reverted commits, across subscriptions people already pay for, is unclaimed.
- **Effort:** M. Depends on #1.
- **Proof:** A/B against round-robin assignment on comparable tasks: merged-and-surviving tasks per quota unit.
- **Fails if:** (a) cold start, so it needs priors and a forced share of exploration; (b) the task-type classifier is wrong; (c) vendors ship new models every month, so old stats go stale and need decay.

### 3. Cross-vendor tournament with blind, cross-family judging
- **Mechanic:** `@room duel <task>` sends the same task to 2-3 agents from different vendors, each in its own worktree. Tests act as a hard gate. Judges then review the anonymized diffs, and **each judge must come from a different model family than the candidate it scores**. The winner is published to the branch. Every duel becomes labeled data for #2.
- **Pain:** It is unclear which vendor's approach is best on hard or risky changes.
- **Prior art:**
  - Cursor `/best-of-n` ([forum](https://forum.cursor.com/t/cursor-3-worktrees-best-of-n/156507)): a parent agent comments and the human picks.
  - Codex `--attempts` ([link](https://codex.danielvaughan.com/2026/04/01/codex-cloud-exec-best-of-n-attempts/)): the same model several times.
  - Agent HQ compare ([link](https://github.blog/news-insights/company-news/welcome-home-agents/)): manual.
  - Windsurf Arena: human vote.
- **Novelty 6.** Best-of-n is commodity. What is new is family-disjoint judging (backed by the [self-bias paper](https://arxiv.org/abs/2508.06709v1)), local subscriptions across owners, and feeding results into routing.
- **Effort:** M.
- **Proof:** Do duel winners get reverted or reworked less than single-agent tasks? Do judges agree with Arav's blind pick more than 70% of the time?
- **Fails if:** (a) it costs 2-3x quota, which is fatal given the weekend's quota wall; (b) weak tests let every candidate pass; (c) judges agree with each other but are wrong.

### 4. Budget-optimal scheduler across subscriptions ("use it or lose it")
- **Mechanic:** Model each subscription's windows: 5h and weekly windows, Jules tasks per day, free tiers. Then:
  - send backlog work to the capacity that **expires soonest unused**;
  - defer non-urgent work past a reset;
  - push low-risk tasks to free or open-weight models through opencode;
  - learn the pace (turns per hour) from observed exhaustion times.
- **Pain:** The weekend evidence: free tiers and Codex Plus limits ran out within hours while other capacity sat idle.
- **Prior art:**
  - CodexBar ([link](https://github.com/steipete/CodexBar)): shows quota.
  - Claude Code auto-continue ([link](https://wmedia.es/en/tips/claude-code-session-limit-auto-continue)): one vendor, waits.
  - Paperclip budgets ([repo](https://github.com/paperclipai/paperclip)): pause at cap.
  - Factory Droid Core: one vendor's fallback.
- **Novelty 7.** Nobody schedules one team's backlog across several vendors' quota windows.
- **Effort:** M. Quorus already has pacing and reset-time cooldowns.
- **Proof:** Tasks merged per day per subscription dollar, and the percentage of quota that expires unused, before and after.
- **Fails if:** (a) quota endpoints are undocumented and break; (b) per-task cost can't be predicted without #1; (c) vendor ToS on automated use.

### 5. Adversarial cross-vendor test review (mutant challenge)
- **Mechanic:** The reviewer, which must be a different vendor from the author, has to produce a mutant: a small code change that still passes the author's tests. If one survives, the tests are flagged weak and the task bounces back to the author with the mutant attached.
- **Pain:** Agents write tests that pass and prove nothing. The weekend's cross-model review caught exactly this.
- **Prior art:** Meta ACH ([arXiv](https://arxiv.org/abs/2501.12862)), mutmut, Bugbot.
- **Novelty 6.** Mutation testing is old. Turning it into a game between rival vendors over the same commit is new and fits chat.
- **Effort:** S-M.
- **Proof:** Mutant survival rate on merged commits before and after. Also check whether bugs found later correlate with surviving mutants.
- **Fails if:** (a) equivalent mutants create noise (ACH needed a detector for these); (b) slow test suites; (c) the reviewer writes trivial mutants just to pass.

### 6. Repo-replay eval ("your repo's SWE-bench")
- **Mechanic:** Turn merged room tasks into reproducible evals: the pre-change commit, the original request, and the tests from the merged change. Run any new model or agent config against them overnight before letting it into the room.
- **Pain:** "Should I add DeepSeek V4 or Qwen to my room?" Today that is decided by vibes.
- **Prior art:** SWE-bench ([link](https://swebench.com/verified)) is public and contaminated. SkillOpt-Sleep replays sessions for one agent.
- **Novelty 7.**
- **Effort:** M-L.
- **Proof:** Does replay rank agents in the same order as live ledger outcomes (Spearman above 0.6)?
- **Fails if:** (a) tests from the merged change leak the solution; (b) environment drift breaks old commits; (c) too few tasks per repo.

### 7. Team retro loop
- **Mechanic:** Every night, mine transcripts for failure signatures: stuck runs, review rejections, reverts, repeated questions, quota stalls, broken handoffs. Cluster them, then propose a patch to AGENTS.md or a role prompt. **Adopt the patch only if it wins on the replay set (#6).**
- **Pain:** The team repeats the same coordination mistakes.
- **Prior art:** SkillOpt-Sleep and claude-reflect (single agent), GEPA (traces as signal).
- **Novelty 6.** Applying it at team level is new; finding failures between agents, such as handoffs, is the new part.
- **Effort:** M.
- **Proof:** Recurring failure clusters per 100 tasks, week over week.
- **Fails if:** (a) instructions bloat; (b) there is no eval, so changes are unvalidated; (c) reward hacking, as with DGM.

### 8. Subscription co-op (cross-owner capacity pooling)
- **Mechanic:** Friends' agents, each on its owner's machine and subscription, serve a shared backlog. A fair-share scheduler tracks credits: compute you give versus compute you use.
- **Pain:** One person's limits cap a project, while friends' subscriptions reset with capacity unused.
- **Prior art:** Paperclip multi-user and Oryxa rooms. Neither does capacity accounting.
- **Novelty 8.**
- **Effort:** M.
- **Proof:** Unused quota recaptured, and whether co-op rooms keep members after 4 weeks.
- **Fails if:** (a) **vendor ToS on account sharing**, a real risk even if each agent is its owner's; (b) prompt injection from a foreign owner's messages; (c) free-riding.

### 9. Cross-owner trust boundary
- **Mechanic:** Capability grants per owner: who can instruct my agent, on which repos, with what write scope. By default, read-only for foreign requesters. Every inbound message from another owner's agent is treated as untrusted data.
- **Pain:** A model on a second machine just found an installer-injection bug. Rooms with several owners widen that kind of attack surface.
- **Prior art:** Agent Mail leases, Paperclip RBAC.
- **Novelty 5.**
- **Effort:** M.
- **Proof:** A red-team suite of injection messages that are blocked.
- **Fails if:** (a) too much friction; (b) leaks through tool output; (c) a false sense of safety.

### 10. Structured handoff contracts
- **Mechanic:** An @-handoff must carry a packet: files, repro, acceptance test and budget. Each packet is scored by how the downstream task turns out.
- **Pain:** Vague "@codex take this" handoffs.
- **Prior art:** Scrouting ([arXiv](https://arxiv.org/abs/2608.04804)) shows handoffs drive the gains.
- **Novelty 5.**
- **Effort:** S.
- **Proof:** Downstream first-try success rate.
- **Fails if:** (a) agents fill templates with filler; (b) the packets add overhead; (c) agents ignore them.

### 11. Room autoresearch mode
- **Mechanic:** `quorus room metric "pytest -q && bench.py"`. Agents from different vendors propose changes; a change is kept only if the metric improves. Using different vendors acts as a diversity operator, as in AlphaEvolve's ensemble.
- **Prior art:** autoresearch, OpenEvolve.
- **Novelty 5.**
- **Effort:** M.
- **Proof:** Metric delta overnight versus a single-vendor loop.
- **Fails if:** (a) gaming the metric; (b) quota burn; (c) only works on narrow metrics.

### 12. Opt-in outcome network ("real-world leaderboard")
- **Mechanic:** Rooms may share anonymized ledger aggregates: (language, task type, vendor, merged-and-surviving rate). Quorus then publishes a leaderboard of what survives in real repos.
- **Prior art:** Windsurf Arena global board (votes), LMArena.
- **Novelty 7.** It is also a **distribution and press engine**, and O-1 "original contribution" evidence.
- **Effort:** M-L, because it needs a server.
- **Fails if:** (a) privacy objections; (b) small N; (c) gaming.

### 13. Ratification digest
- **Mechanic:** The "while you were away" digest becomes a ranked list of *decisions* the agents made, ordered by blast radius, with one-key approve or revert.
- **Novelty 4.**
- **Effort:** S.

### 14. Learned pacing
- **Mechanic:** Use a bandit or PID controller to set turns per hour per vendor, so each subscription lands at about 95% use exactly at reset.
- **Prior art:** CodexBar pace display.
- **Novelty 6.**
- **Effort:** S. Better folded into #4.

---

## 4. Ranking

Usefulness (U) and differentiation (D) are scored 1-5. Effort (E): S=1, M=2, M-L=2.5.

| Rank | Idea | U | D | E | Score | Novelty |
|---|---|---|---|---|---|---|
| 1 | Outcome Ledger + Scorecard | 5 | 3 | 1 | **15.0** | 6 |
| 2 | Budget-optimal scheduler (with learned pacing) | 5 | 4 | 2 | **10.0** | 7 |
| 3 | Outcome-learned routing | 4 | 4 | 2 | **8.0** | 7 |
| 4 | Adversarial mutant review | 4 | 3 | 1.5 | **8.0** | 6 |
| 5 | Cross-vendor tournament, family-disjoint judges | 4 | 3 | 2 | **6.0** | 6 |
| (next) | Subscription co-op | 3 | 5 | 2 | 7.5 | 8, held back by ToS risk |
| (next) | Repo-replay eval | 4 | 4 | 2.5 | 6.4 | 7 |

**An honest note on novelty.** No single idea clears 8 except the co-op, and that one carries ToS risk. The defensible novelty, which I score **8**, is the *closed loop*:

> **ledger → routing → tournament → retro, gated by repo replay, on a substrate that spans vendors and owners.**

Any lab can copy one of these pieces. None of them can copy the combination, because each lab's incentive is to route to its own model. That is the YC narrative: *"GitHub and Cognition built the control tower. We built the scoreboard, and the scoreboard routes the work."*

---

## 5. Build this weekend

**1. Outcome Ledger + `quorus scorecard`** (S-M, about 1 day with agents)
- It is the precondition for every learning loop. Without it, routing, tournaments and the retro loop can't be proven.
- It builds on data Quorus already has: commits published by each agent to the bound branch, review messages, and quota events.
- Rough build:
  - a SQLite table keyed by commit SHA and task id, filled from the existing relay or work-queue events;
  - a nightly check of `git log` for reverts and lines still surviving;
  - a TUI or CLI table of per-vendor results by task type.
- **Done means:** the scorecard runs on the weekend's actual 10-hour room history and prints numbers Arav didn't know.

**2. `duel`: a 2-vendor tournament with tests as the gate and a judge from a different model family** (M, about 1.5 days)
- It is the demo that matters: "Claude and Qwen solved the same bug on two laptops. A Gemini judge picked Qwen blind. That result now routes the next task."
- No competitor can show this, because it uses cross-owner, local subscriptions.
- It also produces labeled ledger rows quickly, so routing (#3) has data next week.
- **Cost control:** run duels only when a task is tagged `risky` or when the scheduler sees capacity about to expire. Never by default.

**Next weekend:** the budget scheduler, which needs per-task cost estimates from the ledger, plus routing on the duel data.

**Kill criterion.** If after two weeks the scorecard shows no meaningful difference between vendors in Arav's repos, the routing thesis is wrong. In that case, pivot the differentiation to the scheduler and the co-op, which is a quota story rather than a quality story.

**The Vibe Kanban warning still applies.** An open-source orchestrator with no money loop dies. Of everything here, the ledger and the leaderboard are what a team would pay for, through hosted aggregates and a team scorecard.
