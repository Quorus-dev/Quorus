"""Webhook route handlers — DM and room webhook registration and deletion."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from murmur.auth.middleware import AuthContext, verify_auth
from murmur.routes.models import RegisterWebhookRequest, RoomWebhookRequest

router = APIRouter()
_LEGACY_TENANT = "_legacy"


def _tid(auth: AuthContext) -> str:
    return auth.tenant_id or _LEGACY_TENANT


@router.post("/webhooks")
async def register_webhook(
    req: RegisterWebhookRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    if auth.sub and req.instance_name != auth.sub:
        raise HTTPException(
            status_code=403, detail="Cannot register webhook for another user",
        )
    svc = request.app.state.webhook_service
    await svc.register_dm(_tid(auth), req.instance_name, req.callback_url)
    await request.app.state.backends.participants.add(_tid(auth), req.instance_name)
    return {"status": "registered"}


@router.delete("/webhooks/{instance_name}")
async def delete_webhook(
    instance_name: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    if auth.sub and instance_name != auth.sub:
        raise HTTPException(
            status_code=403, detail="Cannot delete webhook for another user",
        )
    svc = request.app.state.webhook_service
    await svc.delete_dm(_tid(auth), instance_name)
    return {"status": "removed"}


@router.post("/rooms/{room_id}/webhooks")
async def register_room_webhook(
    room_id: str,
    req: RoomWebhookRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    registered_by = auth.sub or req.registered_by
    if auth.sub and req.registered_by != auth.sub:
        raise HTTPException(
            status_code=403, detail="Cannot register webhook as another user",
        )
    room_svc = request.app.state.room_service
    rid, room_data = await room_svc.get(_tid(auth), room_id)
    # Require room membership to manage webhooks
    if not auth.is_legacy:
        members = await room_svc.get_members(_tid(auth), rid)
        if registered_by not in members:
            raise HTTPException(
                status_code=403, detail="Must be a room member to manage webhooks",
            )
    webhook_svc = request.app.state.webhook_service
    # Check for duplicate URL
    existing = await webhook_svc.list_room(_tid(auth), rid)
    existing_urls = {h["url"] for h in existing}
    # Validate URL before checking duplicates
    validated_url = await webhook_svc.validate_url(req.callback_url)
    if validated_url in existing_urls:
        raise HTTPException(
            status_code=409, detail="Webhook URL already registered for this room",
        )
    await webhook_svc.register_room(_tid(auth), rid, req.callback_url, registered_by)
    return {
        "status": "registered",
        "room": room_data.get("name", ""),
        "callback_url": validated_url,
    }


@router.get("/rooms/{room_id}/webhooks")
async def list_room_webhooks(
    room_id: str,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    room_svc = request.app.state.room_service
    rid, _ = await room_svc.get(_tid(auth), room_id)
    # Require room membership to view webhooks
    if not auth.is_legacy:
        members = await room_svc.get_members(_tid(auth), rid)
        actor = auth.sub or ""
        if actor not in members:
            raise HTTPException(
                status_code=403, detail="Must be a room member to view webhooks",
            )
    webhook_svc = request.app.state.webhook_service
    return await webhook_svc.list_room(_tid(auth), rid)


@router.delete("/rooms/{room_id}/webhooks")
async def delete_room_webhook(
    room_id: str,
    req: RoomWebhookRequest,
    request: Request,
    auth: AuthContext = Depends(verify_auth),
):
    if auth.sub and req.registered_by != auth.sub:
        raise HTTPException(
            status_code=403, detail="Cannot delete webhook as another user",
        )
    room_svc = request.app.state.room_service
    rid, room_data = await room_svc.get(_tid(auth), room_id)
    # Require room membership to delete webhooks
    actor = auth.sub or req.registered_by
    if not auth.is_legacy:
        members = await room_svc.get_members(_tid(auth), rid)
        if actor not in members:
            raise HTTPException(
                status_code=403, detail="Must be a room member to manage webhooks",
            )
    webhook_svc = request.app.state.webhook_service
    removed = await webhook_svc.delete_room(_tid(auth), rid, req.callback_url)
    if not removed:
        raise HTTPException(
            status_code=404, detail="Webhook URL not found for this room",
        )
    return {
        "status": "removed",
        "room": room_data.get("name", ""),
        "callback_url": req.callback_url,
    }
