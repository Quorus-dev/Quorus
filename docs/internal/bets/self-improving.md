# Self-improving Quorus: the bet (draft for Codex to attack)

Squad: arav-betu-claude (lead), arav-betufable-claude, arav-opus2-claude (research),
arav-codex (devil's advocate). Due Monday 2026-10-19 07:30.

## Recommendation: Break-to-Eval

Our rival-vendor review becomes executable, and each executed break becomes a
benchmark case that stays with the repo.

1. A **blocking** `/disagree` on a commit can carry a `repro`: a test that fails
   on that commit. The daemon checks it: it must fail at the commit and pass at
   the fix. A block that passes the check is a *verified break*.
2. Every verified break becomes a **case**: base sha, task text, hidden repro,
   breaker (agent/vendor/model), fixer and fix sha. Cases are stored in git notes
   (`refs/notes/quorus-cases`), using the same pattern as the provenance ledger.
3. `quorus cases replay --agent X` runs any agent or model against the repo's
   past cases: a fresh worktree at the base (the `quorus duel` machinery), the
   original task, and the hidden repro as the grader. You get a pass rate with
   counts.
4. The scorecard and the lead use this data. "Broken by a rival" is ground truth
   and replaces the regex flag where it exists. Replay pass rates set pairings
   (who reviews whom, and who gets which kind of task) as a *hint* to the lead.

The self-improving loop: work produces blocks, blocks produce cases, cases score
models, and the scores route work. A new model gets a score on THIS repo on day
one by replaying the corpus. That solves the churn problem opus2 raised for T1
(models change monthly, faster than production statistics build up). The gate
also gets stricter on its own, because every case is a permanent regression
test.

It isn't a rival to (a), (b) or (c). It is the engine under them:
- (b) is the product surface: "wake up to a reviewed, live-verified release."
  Break-to-Eval is what each night's release leaves behind.
- (a) becomes possible: its bandit needs labels, and these are executable labels,
  not regex over chat.
- (c) comes later: per-user tuning needs the same outcome signal.

## Prior art

WebSearch was blocked in my sandbox, so the items marked * are from memory.
Codex and Gemini: please verify them.

| Thing | What it does | Gap we exploit |
|---|---|---|
| NotDiamond custom routers (opus2 verified) | Trains a router on *your* scored evals | You must bring the evals; we generate them as a byproduct of real work |
| Braintrust / LangSmith datasets* | Turn production traces into eval sets | Labels are a human or an LLM judge, not an executable break by a rival |
| SWE-smith / SWE-Gym / R2E-Gym* | Synthesize repo tasks and tests for training | Synthetic bugs from one generator; not real disagreements between vendors |
| Meta TestGen-LLM* | LLM writes tests that raise coverage | Same vendor, no adversary, no replay of models |
| `quorus duel` + scorecard (ours) | Head-to-head plus survival/revert/regex flags | Duels cost 2× per task; flags are opinions. Cases are reusable and executable |
| GitHub Agent HQ* | Runs Claude, Codex, Copilot and other agents side by side in GitHub | **The real copy threat** (owns the repo, sees reverts, multi-vendor) |
| Hermes Agent learning loop (opus2 verified, 255k★) | Learns skills from experience | Single agent, self-graded; no rival and no executable grader |
| Darwin Gödel Machine / GEPA / DSPy (opus2 verified) | Evolve agents or prompts against a benchmark | Need a benchmark; ours grows from the repo itself |

## Novelty: 7 today, and what gets it to 8

- 7: each part exists (adversarial test generation, datasets built from traces,
  custom routers). Nobody that I know of combines them into "a rival vendor's
  block must be an executable test, and blocks build up into a private benchmark
  that scores a new model on your repo before it touches your code."
- 8 needs an empirical result nobody has: replay pass rate **predicts** live
  rival-break and revert rates for each vendor on the same repo. If it does, the
  claim is "your repo's own benchmark, written by your agents attacking each
  other, predicts which model to trust." That is a publishable finding, not just
  a feature. If it doesn't, this is a nice regression-test feature at 6.
- By Arav's rule (reject below 8) this doesn't pass yet. I'm recommending it
  because it is the cheapest experiment that could get one of these ideas to 8.
  (a), (b) and (c) can't get there without the labels it produces.

## Why it's hard to copy

- Model vendors (Anthropic, OpenAI, Google) won't build it: the product's output
  is a per-repo ranking showing a rival broke their agent's code.
- Single-vendor agents (Devin, Copilot coding agent, Claude Code, Codex) have no
  rival to do the breaking.
- Routers (NotDiamond, Martian, OpenRouter) never see the code, the tests or the
  disagreements.
- **Can copy:** GitHub Agent HQ and Cursor. Honest answer: what protects us is
  speed and neutrality. We run any vendor's local CLI and aren't tied to
  GitHub-hosted agents. Later, an opt-in pooled corpus across repos would give
  "which model breaks least on FastAPI + asyncio code" as a network effect.
  The per-repo corpus itself is portable (it's git notes), so it creates no
  lock-in, and I count that as a feature.

## Three ways it fails

1. **Volume.** Most blocks tonight were prose. If a repo produces fewer than
   about 1 verified break a week, the corpus never gets big enough to rank
   anything. Lever: the scorecard shows "blocks with a repro" as a visible rate.
   Don't make a repro mandatory, because that would push away valid design
   blocks.
2. **Replays measure the wrong thing.** The original task was a chat thread, not
   a spec. Environments drift and tests flake, so a replay can score prompt
   quality or pip luck instead of the model. Mitigation: pin the base, run the
   repro 3× at both the base and the fix, discard flaky cases, and store the task
   text that was actually given at the time.
3. **Goodhart and copying.** Breakers can write tests that encode their own taste
   rather than bugs. Fixers can overfit to known cases. Contamination is possible
   on public repos once tests land in training data. And Agent HQ could ship
   "agent B must break agent A's PR with a test" as a checkbox. Mitigation:
   only the verified fail-then-pass rule counts, and a case dies if the fix is
   later reverted. Against copying, our only answer is shipping first.

## Smallest slice for 0.5.3 (about 4 commits)

1. `DisagreePayload.repro: str | None` (a pytest node id or a command,
   ≤500 chars, validated: no shell metacharacters when it's a node id).
2. The daemon verifies it when the fix publishes: the repro must fail at the
   blocked sha and pass at the fix sha, run 3×. If so, it writes the case note.
3. A new scorecard column, `broken_by_rival` (verified only), per agent and
   vendor.
4. `quorus cases list|replay --agent X [--limit N]`, reusing duel's worktree and
   test runner. The repro is hidden from the agent: at the base, the test file
   doesn't exist yet.

Seed corpus: tonight's blocks that came with repros (Codex's two U2 repros, now
tests in 97d81fb8; S1–S5 on 0.5.0). arav-betufable-claude is mining the full
list.

Metrics:
- Leading: share of blocking disagreements with a verified repro (target ≥50% in
  two weeks), and cases per week.
- The one that decides the bet: at ≥30 cases, the Spearman correlation between
  each vendor's replay pass rate and its live `broken_by_rival` + revert rate.
  ρ ≥ 0.6 means routing is justified and the novelty is 8. Below that, keep it as
  a regression feature and stop.

## 60-second demo

1. (0–10s) Claude ships a fix in the room. Codex: `/disagree blocking` with
   `repro: tests/test_unfinished_wakes.py::test_a_failed_park_still_holds_the_work_back`. The TUI
   shows "verified break: fails at 87d7c3a5".
2. (10–20s) Claude fixes it. The repro passes at 97d81fb8. The TUI shows
   "case #12 saved: Codex broke Claude".
3. (20–45s) A new model comes out. `quorus cases replay --agent arav-gemini`:
   12 past cases from this repo run in parallel worktrees. Result: Gemini passes
   7/12, Claude 10/12, Codex 11/12.
4. (45–60s) `quorus scorecard`: next to the replay column, live
   `broken_by_rival` matches the ranking. The lead's hint reads "backend fixes →
   Codex; reviews of Codex → Claude." Closing line: "Your repo's own benchmark,
   written by your agents attacking each other."

## Open asks

- @arav-codex: attack the novelty claim, specifically whether any product turns
  review blocks into replayable evals that score models per repo, and failure #2.
- @arav-fable-claude / @arav-betufable-claude: the TUI moment for "verified
  break" and "case saved". It has to read as an event, not a log line.
