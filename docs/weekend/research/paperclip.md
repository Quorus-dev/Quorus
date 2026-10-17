# Teardown: Paperclip

## Core Architecture & Mechanism
Paperclip focuses on autonomous agent management via structured lifecycle hooks, persistent state tracking, and isolated workspace execution. It treats agents as first-class team members with role-based permission boundaries and automated supervisor handoffs.

## What It Does Better Than Quorus
- **Hierarchical Agent Supervision:** Paperclip enforces explicit manager-subordinate agent hierarchies with automatic escalation paths when sub-agents encounter blockers or test failures.
- **Visual State Telemetry:** Provides out-of-the-box real-time telemetry dashboards tracking agent CPU, token usage, and step latency per task.

## What Nobody Else Does (Unique Innovations)
- **Zero-Config Agent Sandboxing:** Automatic dynamic creation of ephemeral containerized runtime environments per task without manual workspace setup.
- **Deterministic Prompt Snapshotting:** Captures exact prompt state, context window state, and tool outputs at every checkpoint for reproducible agent audits.

## Takeaways for Quorus
Integrate deeper telemetry metrics into the TUI dashboard and adopt hierarchical escalation triggers for blocked sub-agents.
