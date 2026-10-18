# Per-user self-improvement: shipping slices

Arav's call (2026-10-18): per-user is the core. Each user's Quorus improves and
personalizes itself from that user's own outcomes. It ships in slices of hours
to days, and each slice is a working release with its own live check. Pooled
learning across users comes later.

Honest novelty: per-user tuning alone is about 6. Hermes, ChatGPT memory and
Cursor rules all personalize. The part that's hard to copy is **cross-vendor**:
correcting Claude also teaches Codex and Gemini, and the track record compares
vendors on *your* outcomes. A model vendor only personalizes its own agent.

## Rules for every adaptation

- **Private**: state lives in `~/.quorus/tuning.json` and the user's own repo
  (ledger git notes). Nothing goes to the relay except the one-line
  announcement in the room.
- **Visible**: each adaptation posts one line, `(quorus) adapted t3: <change>
  because <evidence> · undo: quorus tune undo t3`.
- **Reversible**: `quorus tune undo <id>`, plus `quorus tune off` to stop
  adapting entirely and go back to stock behaviour.
- **Measurable**: each learner has a metric and a holdout.

## 0.5.3: visible in the first hour

Foundation (arav-betu-claude): `quorus/tuning.py` holds the store (adaptations,
kind, change, because, evidence, active/undone). It comes with the
`quorus tune [list|undo|off|on]` command and an announce helper. Both learners
below write through it.

L1 Owner rules (arav-betufable-claude):
- A message from the owner to an agent that corrects it ("don't", "never",
  "stop", "always", "from now on") becomes a rule adaptation. The daemon of the
  agent that was addressed records it.
- Every agent's wake prompt gets an `# OWNER PREFERENCES (learned)` block. One
  correction changes every vendor's agent.
- Limits: owner-verified sender only (`trusted_sender`), at most 20 rules, each
  ≤200 chars. Rules sit *below* the hard lines in the Wake Intent and can't
  override them.
- Metric: repeat-correction rate (the same rule stated again) and undo rate
  (under 20% means users want the rules).
- Live check: tell Claude "never put emoji in commit messages", see the `t1`
  line, wake Codex and its prompt has the rule, run `tune undo t1`, and it's
  gone.

L2 Track-record bids (arav-opus2-claude: prior art, metric and build):
- The `@open` bid (`compute_bid_v2`, `reflexd.py:2710`) gets a multiplier
  between ×0.5 and ×1.3, based on two things:
  - this agent's turn outcomes on this machine over the last 24h: clean,
    errored, timed out, vendor failure or paced;
  - its ledger outcomes on the bound repo: approved, flagged, reverted, with
    Laplace smoothing.
- The bid reason carries the evidence ("track: 4✓ 1✗, 2 timeouts/1h").
- Holdout: a random 10% of `@open`s use raw bids.
- Metric: failed turns per `@open` and time to first commit, routed vs holdout.
- Live check: make Gemini fail twice (quota or offline), post `@open`, and
  Gemini's bid is down-weighted with the reason shown. `tune off` restores raw
  bids.

## 0.5.4

- Reviewer pairing, using verified rival breaks as the label (Break-to-Eval,
  `self-improving.md`) plus ledger flags: who reviews whom, per user.
- Quiet what the owner ignores: status and heartbeat kinds the owner never acts
  on are collapsed in the TUI. Fable designs this, with undo.

## 0.5.5+

- The (a) playbook bandit per user, fed by the 0.5.3–0.5.4 labels.
- Opt-in pooled priors across users.
