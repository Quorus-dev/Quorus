# Repo-local task history

`quorus track --repo ~/code/app --owner you --main main` shows tasks assigned
since this feature started recording. `--relay URL` selects another relay's
record; by default it uses the active configuration. `--json` prints the same
aggregate cells. The command is read-only, like the board's agent-card table.

Each cell is one agent, configured model, harness version and task kind in this
owner's repository over 30 days. Unknown model/version information is labelled
`unknown`. Kinds are `test`, `fix`, `feature`, `refactor`, `docs`, `config` or
`unknown`; the owner can put `kind:fix` in an ask to resolve ambiguity before
assignment. The same token from an agent or other trusted member is ignored;
the remaining ask text is still classified by the deterministic keyword rules.

The unit is a task, not a commit. Follow-ups and rescue attempts stay on the
original assignment. Its work deadline is 48 hours; its observation time is nine
days after assignment. Until then it is pending. Published task commits can be
kept, explicitly reverted, or unknown if no longer reachable and unreconciled.
Rebased changes and contiguous squashes are reconciled by patch-id. A git error,
excessive history or exhausted observation budget gives unknown evidence.
Failures, abandonment, escalation and vendor limits have separate counts.

Rates appear only with at least five rate-eligible settled tasks, alongside a
95% Wilson interval. Quota is availability and never enters that denominator.
Escalated original assignments do enter it, as non-kept assignments; their rescue
attempts do not create a separate task for the rescuer. Both counts remain visible,
and the display states this policy. Pending/unknown tasks never become successes.
Time and reported tokens
include rescue attempts and stay separate; missing usage is shown as unknown.
Per-kept costs are shown only when all settled tasks have the corresponding
measurement. Otherwise the measured portion is labelled a partial total, never
an average divided by unmeasured kept tasks.
Commit survival and observed cross-vendor/human approvals are separate measures.
Everything is labelled **observed in this repo, not a ranking**. No routing,
suggestion, assignment probability or permission changes are made.

The daemon appends to the shared git metadata directory's
`quorus/tasks/<scope>.jsonl`, scoped by relay, owner and repository. It stores
identities, timestamps, counters and commit IDs, not ask/reply text. Settlement
appends one fixed outcome when a daemon next observes the due task. CLI and board
reads never append; before a settlement is persisted, they evaluate the same
fixed observation cutoff. Earlier commits are never backfilled as assignments.
