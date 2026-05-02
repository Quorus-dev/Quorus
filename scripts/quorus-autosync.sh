#!/usr/bin/env bash
# Quorus auto-sync: pull/commit/push driver invoked by Claude Code hooks
# and cron. Safe by construction: refuses to auto-commit on main / master.
#
# Usage:
#   quorus-autosync.sh pull        # fetch + rebase current branch
#   quorus-autosync.sh commit      # add + commit if there are changes
#   quorus-autosync.sh push        # push current branch upstream
#   quorus-autosync.sh sync        # pull then commit then push (default)

set -u
mode="${1:-sync}"

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo" || exit 0

branch="$(git symbolic-ref --short HEAD 2>/dev/null || echo HEAD)"

# Safety: never auto-commit/push on main or master.
case "$branch" in
  main|master)
    if [ "$mode" = "commit" ] || [ "$mode" = "sync" ]; then
      echo "[autosync] on $branch — skipping auto-commit (safety). switch to a feature branch." >&2
      mode="pull"
    fi
    ;;
esac

do_pull() {
  git fetch --quiet origin 2>/dev/null || true
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>/dev/null || true)"
  if [ -n "$upstream" ]; then
    git pull --rebase --autostash --quiet 2>&1 | grep -v "^Already up to date" || true
  fi
}

do_commit() {
  # Refuse to stage anything that looks like a secret.
  if git diff --cached --name-only | grep -E '\.(env|pem|key)$|secrets?/' >/dev/null 2>&1; then
    echo "[autosync] aborting — staged file looks like a secret" >&2
    return 1
  fi
  if git diff --name-only | grep -E '\.(env|pem|key)$|secrets?/' >/dev/null 2>&1; then
    echo "[autosync] aborting — modified file looks like a secret" >&2
    return 1
  fi

  git add -A 2>/dev/null
  if git diff --cached --quiet; then
    return 0
  fi

  files_changed="$(git diff --cached --name-only | wc -l | tr -d ' ')"
  summary="$(git diff --cached --stat | tail -1 | sed 's/^ *//')"
  msg="auto: sync ${files_changed} files

${summary}

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

  git commit --quiet -m "$msg" || return 1
  return 0
}

do_push() {
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>/dev/null || true)"
  if [ -z "$upstream" ]; then
    git push --quiet -u origin "$branch" 2>&1 | tail -3 || true
  else
    git push --quiet 2>&1 | tail -3 || true
  fi
}

case "$mode" in
  pull)   do_pull ;;
  commit) do_commit ;;
  push)   do_push ;;
  sync)   do_pull && do_commit && do_push ;;
  *)
    echo "[autosync] unknown mode: $mode" >&2
    exit 2
    ;;
esac
