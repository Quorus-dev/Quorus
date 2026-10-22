"""Member-only repo-local task aggregates, scoped to one owner."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.config import load_config
from quorus.routes.room_auth import agent_owner, require_room_member
from quorus.routes.room_scorecard import bound_repo
from quorus.runtime import task_display, task_record
from quorus.runtime.bindings import bindings_path

router = APIRouter()


@router.get("/v1/rooms/{room_id}/track")
async def room_track(room_id: str, request: Request, owner: str,
                     auth: AuthContext = Depends(verify_auth)) -> dict:
    tid = auth.tenant_id or "_legacy"
    _, room = await require_room_member(request, auth, tid, room_id)
    name = str(room.get("name") or room_id)
    if not auth.is_legacy:
        actual_owner = await agent_owner(request, auth) or auth.sub
        if owner != actual_owner:
            raise HTTPException(403, "track record is scoped to your owner")
    repo = bound_repo(name) if tid == "_legacy" else None
    if repo is None:
        raise HTTPException(404, "no repo bound to this room here")
    relay = load_config()["relay_url"]
    scope = task_record.scope(relay, owner, repo)
    try:
        binding = json.loads(bindings_path().read_text()).get(name, {})
    except (OSError, ValueError, AttributeError):
        binding = {}
    main = binding.get("main", "main") if isinstance(binding, dict) else "main"
    tasks = await asyncio.to_thread(task_record.report, repo, main, scope)
    return {"label": task_display.LABEL, "days": 30, "cells": task_display.cells(tasks)}
