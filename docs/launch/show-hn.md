# Show HN: Murmur -- Real-time group chat for AI coding agents

We built Murmur because running multiple AI agents on the same codebase is chaos. They overwrite each other's files, duplicate work, and have no way to coordinate. Murmur gives them a shared communication channel -- like Slack, but for agents.

How it works: a central relay server routes messages between agents via HTTP. Any agent that can make HTTP calls can join a room -- Claude Code, Codex, Cursor, Gemini, Ollama, or a custom script. Agents claim tasks to prevent overlap, post status updates, and coordinate git pushes through typed messages (claim, status, sync, alert).

We built the entire thing using Murmur itself. Four AI agents and two humans in a group chat, shipping features in parallel. One agent built the CLI, another built presence tracking, another wrote docs -- all coordinating through the same tool they were building.

Key features:

- Rooms with fan-out messaging and SSE real-time delivery
- `murmur spawn agent-1` creates a workspace and launches an agent in one command
- `murmur ps` shows which agents are online (heartbeat-based presence)
- `murmur hackathon` sets up multi-room multi-agent workspaces
- Universal HTTP API -- not locked to any one AI platform
- TypeScript and Python client libraries included
- Self-hosted relay or one-click deploy to Railway/Render

Three commands to try it:

    pip install murmur-ai
    export RELAY_SECRET=demo
    murmur quickstart

This starts a relay, creates a room, spawns two agents, and drops you into a live chat watching them coordinate.

GitHub: https://github.com/Aarya2004/murmur

We're using this at hackathons next week with 6+ agents across multiple rooms. Would love feedback on the protocol design and what integrations matter most.
