"""MCP tools to hand a git branch to teammates on other machines.

Thin wrappers over ``quorus.bundle_client`` (the same code as
`quorus share-branch` / `quorus fetch`). Fetched code only lands under
``refs/quorus/<uploader>/<branch>``: never a branch, checkout or merge.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from quorus import bundle_client as _bc
from quorus_mcp.server import mcp


async def _bearer() -> str:
    from quorus_mcp import server

    if server.API_KEY:
        return await server._get_bearer_token()
    return await asyncio.to_thread(_bc.bearer, server.RELAY_URL,
                                   secret=server.RELAY_SECRET, name=server.INSTANCE_NAME)


def _repo(repo_path: str) -> Path:
    repo = Path(repo_path or ".").expanduser().resolve()
    if not (repo / ".git").exists():
        raise _bc.BundleClientError(f"not a git repo: {repo}")
    return repo


async def share_branch_impl(room_id: str, repo_path: str = ".", branch: str = "",
                            base: str = "", full: bool = False) -> str:
    from quorus_mcp import server

    try:
        repo = _repo(repo_path)
        data, name = await asyncio.to_thread(
            _bc.make_bundle, repo, branch=branch or None, base=base or None, full=full)
        meta = await asyncio.to_thread(_bc.upload, server.RELAY_URL, await _bearer(),
                                       room_id, data)
    except _bc.BundleClientError as exc:
        return f"Error: {exc}"
    return (f"Shared {name} @{meta['head'][:7]} as {meta['id']} ({meta['size']} bytes). "
            f"Teammates fetch it with fetch_bundle(room_id={room_id!r}, "
            f"bundle_id={meta['id']!r}). It lasts 24h; a relay restart drops it.")


async def fetch_bundle_impl(room_id: str, bundle_id: str, repo_path: str = ".") -> str:
    from quorus_mcp import server

    try:
        repo = _repo(repo_path)
        data, meta = await asyncio.to_thread(_bc.download, server.RELAY_URL,
                                             await _bearer(), room_id, bundle_id)
        got = await asyncio.to_thread(_bc.fetch_into, repo, data, meta)
    except _bc.BundleClientError as exc:
        return f"Error: {exc}"
    return (f"Fetched @{got.uploader}'s {got.branch} into {got.ref} (head {got.head[:7]}). "
            f"Review with `git show {got.head[:7]}` or `git diff main...{got.ref}`. "
            "No branch was created or checked out.")


@mcp.tool()
async def share_branch(room_id: str, repo_path: str = ".", branch: str = "",
                       base: str = "", full: bool = False) -> str:
    """Share a git branch with teammates on other machines, through the room.

    Sends only the commits ``base`` (default main) lacks; set full=True when a
    teammate reports a missing base commit. Use it before asking someone on
    another machine to review a commit they can't `git show`."""
    return await share_branch_impl(room_id, repo_path, branch, base, full)


@mcp.tool()
async def fetch_bundle(room_id: str, bundle_id: str, repo_path: str = ".") -> str:
    """Fetch a branch a teammate shared (📦 ... quorus fetch b_...) into a
    read-only ref refs/quorus/<them>/<branch>, so `git show` works on it."""
    return await fetch_bundle_impl(room_id, bundle_id, repo_path)
