# Teardown: claude-squad

## Core Architecture & Mechanism
A lightweight orchestration framework for spawning and coordinating multiple parallel Claude instances across isolated tmux panes or background processes sharing a git repository.

## What It Does Better Than Quorus
- **Zero-Infrastructure Setup:** Runs purely on local terminal utilities (tmux, bash, git) without requiring external relay servers or database backends.
- **Immediate Terminal Transparency:** Direct view of raw agent command outputs and terminal interactions in split panes.

## What Nobody Else Does (Unique Innovations)
- **Tmux-Native Inter-Agent Signaling:** Uses terminal window focus and custom bell signals to coordinate agent turns and notifications without network overhead.

## Takeaways for Quorus
Provide an optional lightweight local execution mode using tmux panes for developers who want offline swarm orchestration without relay infrastructure.
