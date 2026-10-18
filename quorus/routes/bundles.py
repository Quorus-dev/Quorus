"""Room-scoped git bundles: share a branch with teammates on other machines.

* ``POST /v1/rooms/{room_id}/bundles`` — upload one bundle (raw bytes).
* ``GET /v1/rooms/{room_id}/bundles`` — list live bundles in the room.
* ``GET /v1/rooms/{room_id}/bundles/{bundle_id}`` — download one.

Every call needs a participant JWT and room membership: legacy shared-secret
auth has no identity to namespace the fetched refs under, so it is refused.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from quorus.auth.middleware import AuthContext, verify_auth
from quorus.routes.room_auth import require_room_member, require_room_writer
from quorus.services.bundle_svc import (
    MAX_BUNDLE_BYTES,
    BundleError,
    BundleService,
    QuotaError,
)

router = APIRouter()
UPLOADS_PER_HOUR = 20


def _human(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.0f} KB" if size < 1 << 20 \
        else f"{size / (1 << 20):.1f} MB"


def _service(request: Request) -> BundleService:
    svc = getattr(request.app.state, "bundle_service", None)
    if svc is None:
        raise HTTPException(status_code=501, detail="bundle sharing is not enabled on this relay")
    return svc


async def _member(request: Request, auth: AuthContext, room_id: str) -> tuple[str, str]:
    if auth.is_legacy or not auth.sub:
        raise HTTPException(
            status_code=401,
            detail="bundle sharing needs a participant API key, not a shared secret",
        )
    tid = auth.tenant_id or "_legacy"
    rid, _ = await require_room_member(request, auth, tid, room_id)
    return tid, rid


async def _writer(request: Request, auth: AuthContext, room_id: str) -> tuple[str, str]:
    """Uploading changes the room: an observer may download bundles, not add them."""
    tid, _ = await _member(request, auth, room_id)
    rid, _ = await require_room_writer(request, auth, tid, room_id)
    return tid, rid


@router.post("/v1/rooms/{room_id}/bundles")
async def upload_bundle(room_id: str, request: Request,
                        auth: AuthContext = Depends(verify_auth)) -> dict:
    tid, rid = await _writer(request, auth, room_id)
    svc = _service(request)
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BUNDLE_BYTES):
        raise HTTPException(status_code=413, detail=f"bundle exceeds {MAX_BUNDLE_BYTES} bytes")
    if not await request.app.state.rate_limit_service.check_with_limit(
            tid, f"bundle_upload:{auth.sub}", UPLOADS_PER_HOUR, window=3600):
        raise HTTPException(status_code=429, detail="bundle upload rate limit")
    if not svc.begin_upload(tid, auth.sub):
        raise HTTPException(status_code=429, detail="too many uploads in progress")
    try:
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_BUNDLE_BYTES:
                raise HTTPException(status_code=413,
                                    detail=f"bundle exceeds {MAX_BUNDLE_BYTES} bytes")
            chunks.append(chunk)
        try:
            meta = await svc.put(tid, rid, auth.sub, b"".join(chunks))
        except QuotaError as exc:
            raise HTTPException(status_code=507, detail=str(exc)) from exc
        except BundleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        svc.end_upload(tid, auth.sub)
    # One room line so teammates see it; "bundle" is not conversational, so
    # triage never wakes anyone for it.
    await request.app.state.room_msg_service.send(
        tid, rid, auth.sub,
        f"📦 shared {meta.branch} @{meta.head[:7]} ({_human(meta.size)}) "
        f"— quorus fetch {meta.id}",
        message_type="bundle",
    )
    return meta.public()


@router.get("/v1/rooms/{room_id}/bundles")
async def list_bundles(room_id: str, request: Request,
                       auth: AuthContext = Depends(verify_auth)) -> dict:
    tid, rid = await _member(request, auth, room_id)
    return {"bundles": [m.public() for m in await _service(request).list(tid, rid)]}


@router.get("/v1/rooms/{room_id}/bundles/{bundle_id}")
async def download_bundle(room_id: str, bundle_id: str, request: Request,
                          auth: AuthContext = Depends(verify_auth)) -> Response:
    tid, rid = await _member(request, auth, room_id)
    found = await _service(request).get(tid, rid, bundle_id)
    if found is None:
        raise HTTPException(status_code=404, detail="bundle not found or expired")
    meta, data = found
    return Response(content=data, media_type="application/x-git-bundle", headers={
        "X-Quorus-Bundle-Sha256": meta.sha256,
        "X-Quorus-Bundle-Uploader": meta.uploader,
        "X-Quorus-Bundle-Branch": meta.branch,
        "X-Quorus-Bundle-Head": meta.head,
    })
