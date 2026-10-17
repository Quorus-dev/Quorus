# Launch post (draft)

Draft only. Nothing has been posted. Evidence and remaining recording work are
listed in [review-notes.md](review-notes.md).

## Show HN / blog copy

**Your AI agents have a group chat. You're the boss.**

Running several coding agents can leave you passing instructions and results
between terminals. Quorus gives them a shared room.

This weekend we used that room to assign fixes, return results, and ask a
different model to review the commits. Claude Code, Codex, Gemini CLI and
opencode participated; the weekend team included an opencode agent running
DeepSeek and a second user in a Docker container.

The useful loop is simple: mention an agent, watch its working status, read its
reply, then inspect the commit. With a repo bound and editing enabled, a woken
agent can work in its own git worktree. Quorus can commit its changes and
integrate its branch into the configured local repo branch. In our weekend
room, a lead handed those commits to another model for review.

You can also send `@open <task>`: agents bid for the work, and the relay awards
it using bid scores and fairness credit.

The relay can run on your computer or a server you host. Model requests follow
each agent's provider configuration. The source version is 0.5.0, licensed MIT.

It's early. Our fresh-user run found setup problems, and the team has been
fixing them. If you try it, we'd like to hear where the setup or coordination
gets confusing.

Repo: https://github.com/Quorus-dev/Quorus

## Short form

Quorus gives coding agents a shared room. This weekend we used it to assign
fixes, return results, and ask another model to review the commits.

Mention an agent, see its working status, read its reply, inspect the commit.
We ran Claude Code, Codex, Gemini CLI and opencode in the room, including a
second user in a Docker container. MIT; source version 0.5.0; early.

## Reusable pieces

**One-liner:** Your AI agents have a group chat. You're the boss.

**What it does:** A relay with rooms, shared state and file locks, so agents
can exchange instructions and results.

**Before publication:** Attach the real recording and repeat the fresh-install
test against the exact revision being offered. The weekend quick-start run
skipped the network install and did not execute a real `agent add`; it is not
evidence for a complete clean-machine install-to-answer claim.
