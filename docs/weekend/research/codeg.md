# Teardown: codeg

## Core Architecture & Mechanism
Codeg is a lightweight graph-based code generation engine that constructs internal dependency graphs of codebases before performing multi-file refactoring or feature additions.

## What It Does Better Than Quorus
- **Dependency-Aware Refactoring:** Ensures no dangling references or broken imports across packages by validating edits against an live AST dependency graph.
- **Lightning-Fast CLI Execution:** Minimal overhead CLI wrapper optimized for sub-second code generation and test execution loops.

## What Nobody Else Does (Unique Innovations)
- **Live Graph Mutation Preview:** Visualizes how proposed code changes propagate through project dependencies before applying edits to disk.

## Takeaways for Quorus
Integrate graph-based dependency checking into our pre-commit and refactoring workflows to prevent cross-package regressions.
