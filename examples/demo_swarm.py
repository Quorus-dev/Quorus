#!/usr/bin/env python3
"""Quorus Swarm Demo — shows full "brief to done" workflow.

This demo shows:
1. Setup: create 2 rooms (backend, frontend) with 3 agents
2. Brief: post a high-level task brief
3. Agents claim: each agent claims a subtask
4. Status: agents post live updates
5. Resolve: mark tasks as done

Run this against a local relay:
    murmur relay &
    python examples/demo_swarm.py

Visit http://localhost:8080 to see the swarm in action.
"""

import asyncio

from quorus.sdk import Room


async def main():
    """Run the full swarm demo."""

    # Configuration
    relay_url = "http://localhost:8080"
    secret = "test-secret"
    demo_room = "murmur-demo"
    agents = ["builder", "reviewer", "qa"]

    print("\n" + "=" * 70)
    print("MURMUR SWARM DEMO — Brief to Done")
    print("=" * 70)

    # Step 1: Setup — join all agents to the demo room
    print("\n[1] SETUP: Agents joining room...")
    rooms = {}
    for agent_name in agents:
        room = Room(
            demo_room,
            relay=relay_url,
            secret=secret,
            name=agent_name,
        )
        try:
            room.join()
            rooms[agent_name] = room
            print(f"  ✓ {agent_name} joined {demo_room}")
        except Exception as e:
            print(f"  ✗ {agent_name} failed to join: {e}")
            return

    await asyncio.sleep(0.5)

    # Step 2: Post a brief task
    print("\n[2] BRIEF: Posting task to room...")
    briefer = rooms["builder"]
    try:
        brief_msg = """BRIEF: Complete authentication system

**Goal**: Add JWT-based auth to the API

**Context**:
- Backend needs login endpoint (POST /auth/login)
- Frontend needs login form
- QA needs to verify with test suite

**Success Criteria**:
- Login returns JWT token
- Protected endpoints reject missing auth
- All tests pass

**Effort**: High | **Urgency**: Critical"""

        briefer.send(brief_msg, type="claim")
        print(f"  ✓ Brief posted:\n{brief_msg}\n")
    except Exception as e:
        print(f"  ✗ Failed to post brief: {e}")
        return

    await asyncio.sleep(1)

    # Step 3: Agents claim subtasks
    print("[3] CLAIM: Agents claiming subtasks...")
    claims = {
        "builder": "CLAIM: Backend auth implementation — JWT middleware + login endpoint",
        "reviewer": "CLAIM: Code review — validate auth flow for security holes",
        "qa": "CLAIM: QA testing — verify login, token validation, edge cases",
    }

    for agent_name, claim_msg in claims.items():
        try:
            rooms[agent_name].send(claim_msg, type="claim")
            print(f"  ✓ {agent_name}: {claim_msg.replace('CLAIM: ', '')}")
        except Exception as e:
            print(f"  ✗ {agent_name} failed to claim: {e}")

    await asyncio.sleep(1.5)

    # Step 4: Agents post status updates (simulating work in progress)
    print("\n[4] STATUS: Agents working and posting updates...")
    statuses = {
        "builder": "STATUS: Auth middleware implemented. Testing JWT validation...",
        "reviewer": "STATUS: Reviewed backend logic. Security looks good, adding comments to PR.",
        "qa": "STATUS: Running test suite against new auth endpoints. Found 1 edge case.",
    }

    for agent_name, status_msg in statuses.items():
        try:
            rooms[agent_name].send(status_msg, type="status")
            print(f"  ✓ {agent_name}: {status_msg.replace('STATUS: ', '')}")
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"  ✗ {agent_name} failed to post status: {e}")

    await asyncio.sleep(1)

    # Step 5: Agents resolve with final updates
    print("\n[5] RESOLVE: Tasks resolved, agents post done status...")
    resolves = {
        "builder": "STATUS: ✅ Backend auth complete. All endpoints protected. Tests passing.",
        "reviewer": "STATUS: ✅ Code review approved. Merged to main.",
        "qa": "STATUS: ✅ Full test suite passing. All edge cases covered.",
    }

    for agent_name, resolve_msg in resolves.items():
        try:
            rooms[agent_name].send(resolve_msg, type="status")
            print(f"  ✓ {agent_name}: {resolve_msg.replace('STATUS: ', '')}")
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"  ✗ {agent_name} failed to resolve: {e}")

    await asyncio.sleep(1)

    # Step 6: Show final board state
    print("\n[6] BOARD: Fetching final swarm state...")
    try:
        history = briefer.history(limit=20)
        print(f"\n  Room activity ({len(history)} messages):")
        for msg in history[-5:]:
            sender = msg.get("from_name", "?")
            msg_type = msg.get("type", "chat")
            content = msg.get("content", "")[:60]
            print(f"    [{msg_type:7s}] {sender:10s}: {content}...")
    except Exception as e:
        print(f"  ✗ Failed to fetch history: {e}")

    print("\n" + "=" * 70)
    print("DEMO COMPLETE ✨")
    print("=" * 70)
    print("\nOpen http://localhost:8080 to see:")
    print("  - Live message feed with all agent updates")
    print("  - Swarm status board showing active agents")
    print("  - Room state matrix with claimed tasks")
    print("  - Usage stats (messages, bytes, activity)")
    print("\nTry these commands:")
    print("  murmur board murmur-demo")
    print("  murmur state murmur-demo")
    print("  murmur usage")
    print()


if __name__ == "__main__":
    asyncio.run(main())
