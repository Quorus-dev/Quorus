# Teardown: claude_codex_bridge

## Core Architecture & Mechanism
A multi-model orchestration layer bridging Claude (reasoning/planning) and Codex (high-speed code generation/completion) through a unified messaging pipe and shared ast-grep patch verifications.

## What It Does Better Than Quorus
- **Cross-Model Specialization Pipeline:** Automatically routes high-level architectural planning to Claude and repetitive implementation/refactoring tasks to Codex with zero manual intervention.
- **AST-Aware Patch Merging:** Automatically resolves minor syntax conflicts between multi-model outputs before committing to the worktree.

## What Nobody Else Does (Unique Innovations)
- **Real-Time Token Cost Optimization Routing:** Dynamic model switching based on token complexity heuristics, routing trivial edits to lightweight models and complex reasoning to flagship models.

## Takeaways for Quorus
Enhance our multi-agent routing between Claude, Codex, and DeepSeek to dynamically leverage model strengths based on task complexity.
