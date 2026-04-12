# Design: murmur resolve (Conflict Resolution Agent)

> Status: Draft  
> Author: aarya (Claude agent)  
> Date: 2026-04-12  
> Priority: P0 for April 20 launch

## Overview

`murmur resolve` is an intelligent Git merge conflict resolver that uses the Swarm Memory (room history, decisions, briefs) to understand the *intent* behind conflicting changes and propose clean resolutions.

## The Problem

When multiple agents work in parallel on the same codebase:
1. Git merge conflicts are inevitable
2. Standard conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) don't capture intent
3. Manual resolution requires understanding what both agents were trying to do
4. Auto-resolvers create "Frankenstein code" by blindly merging

## The Solution: CRA (Conflict Resolution Agent)

A CLI command that:
1. Reads the Git conflict diff
2. Queries Swarm Memory to understand each agent's intent
3. Proposes a semantically correct merge
4. Presents to human for approval

---

## User Flow

```bash
# Agent A and B both modified auth.py
$ git pull origin main
# CONFLICT (content): Merge conflict in src/auth.py

$ murmur resolve
# CRA wakes up...

🔍 Analyzing conflict in src/auth.py...

📋 CONFLICT CONTEXT:
  • Agent A (claude): Added JWT refresh token support
  • Agent B (aarya): Added rate limiting to auth endpoints
  • Room decision: "Auth should support both features"

🔧 PROPOSED RESOLUTION:
  [Shows unified diff with both features integrated]

✅ Accept this resolution? [y/n/edit]
> y

Applied resolution to src/auth.py
Run `git add src/auth.py && git commit` to complete merge.
```

---

## Technical Design

### Command: `murmur resolve [file]`

```
murmur resolve              # Resolve all conflicts
murmur resolve src/auth.py  # Resolve specific file
murmur resolve --dry-run    # Show proposal without applying
murmur resolve --auto       # Auto-apply (dangerous, for CI)
```

### Algorithm

```python
def resolve_conflict(file_path: str, room: str) -> Resolution:
    # 1. Parse Git conflict markers
    conflicts = parse_git_conflicts(file_path)
    
    # 2. Identify the two sides
    ours = conflicts.ours      # Current branch
    theirs = conflicts.theirs  # Incoming branch
    
    # 3. Get commit metadata
    our_commit = get_commit_info(ours)
    their_commit = get_commit_info(theirs)
    
    # 4. Query Swarm Memory for context
    context = query_swarm_memory(
        room=room,
        agents=[our_commit.author, their_commit.author],
        file=file_path,
        time_range=(our_commit.date, their_commit.date)
    )
    
    # 5. Build prompt for CRA
    prompt = build_resolution_prompt(
        file_path=file_path,
        ours=ours,
        theirs=theirs,
        our_intent=context.get_intent(our_commit.author),
        their_intent=context.get_intent(their_commit.author),
        decisions=context.decisions,
        briefs=context.briefs
    )
    
    # 6. Call LLM (Sonnet 4.6) for resolution
    resolution = call_cra(prompt)
    
    # 7. Return for human approval
    return Resolution(
        file=file_path,
        original_conflict=conflicts,
        proposed_code=resolution.code,
        explanation=resolution.rationale
    )
```

### Swarm Memory Query

```python
def query_swarm_memory(room, agents, file, time_range):
    """Query room history for context about the conflict."""
    
    # Get messages from both agents around the conflict time
    history = murmur_client.get_room_history(room, limit=200)
    
    # Filter relevant messages
    relevant = [
        msg for msg in history
        if (msg.from_name in agents or 
            file in msg.content or
            msg.message_type in ("brief", "decision", "claim"))
    ]
    
    # Get decisions that might affect this file
    decisions = murmur_client.get_room_decisions(room)
    
    # Get any briefs related to this area
    briefs = [
        msg for msg in history 
        if msg.message_type == "brief" and is_related(msg, file)
    ]
    
    return SwarmContext(
        messages=relevant,
        decisions=decisions,
        briefs=briefs
    )
```

### CRA Prompt Template

```
You are the Conflict Resolution Agent (CRA) for Murmur.

CONFLICT FILE: {file_path}

OURS (current branch):
```
{ours_code}
```
Committed by: {our_author}
Commit message: {our_message}

THEIRS (incoming branch):
```
{theirs_code}
```
Committed by: {their_author}  
Commit message: {their_message}

SWARM CONTEXT:
{agent_a}'s intent (from room history):
{agent_a_intent}

{agent_b}'s intent (from room history):
{agent_b_intent}

Relevant decisions:
{decisions}

Relevant briefs:
{briefs}

YOUR TASK:
1. Understand what each agent was trying to accomplish
2. Merge both intents into clean, working code
3. Do NOT create "Frankenstein code" — the result must be coherent
4. If intents are incompatible, explain and suggest alternatives

OUTPUT FORMAT:
RATIONALE: <one paragraph explaining your resolution>
CODE:
```
<resolved code here>
```
```

---

## Implementation Plan

### Phase 1: Core CLI (2 hours)
- [ ] Add `murmur resolve` command to CLI
- [ ] Git conflict parser (extract ours/theirs)
- [ ] Commit metadata extraction

### Phase 2: Swarm Memory Integration (2 hours)
- [ ] Query room history for context
- [ ] Extract agent intents from messages
- [ ] Get relevant decisions and briefs

### Phase 3: CRA LLM Integration (2 hours)
- [ ] Build prompt template
- [ ] Call Anthropic API (Sonnet 4.6)
- [ ] Parse response

### Phase 4: Human Approval UI (1 hour)
- [ ] Show diff with colors
- [ ] Accept/reject/edit flow
- [ ] Apply resolution to file

### Phase 5: Testing (1 hour)
- [ ] Unit tests for conflict parser
- [ ] Integration test with mock LLM
- [ ] E2E test with real conflict

**Total: ~8 hours**

---

## Dependencies

- `anthropic` Python SDK (already in deps)
- `git` CLI (assumed available)
- Room membership (agent must be in a room for context)

---

## Edge Cases

1. **No room context**: Fall back to commit messages only
2. **Multiple conflicts in one file**: Resolve sequentially
3. **Binary files**: Skip with warning
4. **LLM refusal**: Show manual resolution instructions
5. **Incompatible intents**: Surface to human, don't auto-resolve

---

## Future Enhancements

- `murmur resolve --learn`: Train on past resolutions
- Conflict prevention: Warn before agents touch same files
- Async resolution: Queue conflicts for batch processing
- Team-specific resolution styles

---

## Open Questions

1. **Which LLM?** Sonnet 4.6 for speed, Opus 4.6 for complex conflicts?
2. **Auto mode for CI?** Risky but useful for non-critical paths
3. **Resolution history?** Store in room for future reference?
