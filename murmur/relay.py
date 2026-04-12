"""Murmur Relay — FastAPI app setup, lifespan, middleware, and router wiring.

Route handlers live in relay_routes.py. Auth/admin routes in their own packages.
"""

import asyncio
import json
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.middleware.base import BaseHTTPMiddleware

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if LOG_LEVEL == "DEBUG"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, LOG_LEVEL, logging.INFO)
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger("murmur.relay")

RELAY_SECRET = os.environ.get("RELAY_SECRET", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
MESSAGES_FILE = os.environ.get("MESSAGES_FILE", "messages.json")
JWT_SECRET = os.environ.get("JWT_SECRET", "")
BOOTSTRAP_SECRET = os.environ.get("BOOTSTRAP_SECRET", "")

# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

REDIS_URL = os.environ.get("REDIS_URL", "")

# When Postgres is configured, JWT auth is the primary mechanism.
if DATABASE_URL:
    if not JWT_SECRET or len(JWT_SECRET) < 32:
        raise SystemExit(
            "JWT_SECRET must be set (min 32 chars) when DATABASE_URL is configured. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    if not REDIS_URL:
        raise SystemExit(
            "REDIS_URL must be set when DATABASE_URL is configured. "
            "Redis is required for session state, rate limiting, and presence "
            "in production mode. Example: REDIS_URL=redis://localhost:6379/0"
        )
    if not BOOTSTRAP_SECRET:
        logger.warning(
            "BOOTSTRAP_SECRET is not set — tenant creation via the bootstrap "
            "endpoint will be unavailable. Set it if you need to create tenants."
        )
elif not RELAY_SECRET:
    # No Postgres and no legacy secret — nothing will work
    raise SystemExit(
        "Neither RELAY_SECRET nor DATABASE_URL is set. "
        "Set at least one to start the relay."
    )


# ---------------------------------------------------------------------------
# Persistence helpers (JSON file — used when Postgres is not configured)
# ---------------------------------------------------------------------------


def _write_atomic(path: str, data: bytes) -> None:
    dir_name = os.path.dirname(os.path.abspath(path))
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, path)
    except OSError:
        logger.error("Failed to save state to %s", path)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _snapshot_state() -> dict:
    """Snapshot in-memory backend state for JSON persistence.

    Only used in dev/test mode (no Redis). Accesses InMemoryBackend
    internals directly — this is intentional and will not run when
    Redis is configured.
    """
    if not hasattr(app.state, "backends"):
        return {}
    backends = app.state.backends
    # Skip if using Redis backends (persistence is handled by Redis)
    if not hasattr(backends.messages, "_queues"):
        return {}
    # Messages: convert tuple keys to string keys for JSON
    messages = {}
    for (tid, name), msgs in backends.messages._queues.items():
        key = f"{tid}:{name}"
        messages[key] = list(msgs)
    # Rooms
    rooms_data = {}
    for (tid, rid), rdata in backends.rooms._rooms.items():
        members = rdata.get("members", {})
        rooms_data[rid] = {
            "name": rdata.get("name", ""),
            "created_by": rdata.get("created_by", ""),
            "members": sorted(members.keys()) if isinstance(members, dict) else sorted(members),
            "member_roles": members if isinstance(members, dict) else {},
            "tenant_id": rdata.get("tenant_id", "_legacy"),
            "created_at": rdata.get("created_at", ""),
        }
    # Room history
    history = {}
    for (tid, rid), msgs in backends.room_history._history.items():
        history[rid] = list(msgs)
    # Room webhooks
    room_webhooks = {}
    for (tid, rid), hooks in backends.webhooks._room_hooks.items():
        room_webhooks[rid] = list(hooks)
    # Presence
    presence_data = {}
    for (tid, name), entry in backends.presence._entries.items():
        key = f"{tid}:{name}"
        presence_data[key] = dict(entry)
    # DM webhooks
    dm_webhooks = {}
    for (tid, name), url in backends.webhooks._dm_hooks.items():
        dm_webhooks[name] = url
    return {
        "messages": messages,
        "participants": [],
        "analytics": {
            "total_sent": 0,
            "total_delivered": 0,
            "per_participant": {},
            "hourly_volume": {},
        },
        "rooms": rooms_data,
        "room_history": history,
        "room_webhooks": room_webhooks,
        "presence": presence_data,
        "webhooks": dm_webhooks,
    }


def _apply_loaded_state(data: dict) -> None:
    """Load persisted state into backends."""
    if not hasattr(app.state, "backends"):
        return
    backends = app.state.backends
    _LEGACY_TENANT = "_legacy"
    # Messages
    for key, msgs in data.get("messages", {}).items():
        parts = key.split(":", 1)
        tid = parts[0] if len(parts) == 2 else _LEGACY_TENANT
        name = parts[1] if len(parts) == 2 else parts[0]
        backends.messages._queues[(tid, name)] = msgs
    # Rooms
    for rid, rdata in data.get("rooms", {}).items():
        tid = rdata.get("tenant_id", _LEGACY_TENANT)
        members_list = rdata.get("members", [])
        member_roles = rdata.get("member_roles", {})
        # Build members dict from list + roles
        members = {}
        for m in members_list:
            members[m] = member_roles.get(m, "member")
        room_data = {
            "name": rdata["name"],
            "created_by": rdata["created_by"],
            "members": members,
            "tenant_id": tid,
            "created_at": rdata["created_at"],
        }
        backends.rooms._rooms[(tid, rid)] = room_data
        if rdata["name"]:
            backends.rooms._name_index[(tid, rdata["name"])] = rid
    # Room history
    for rid, msgs in data.get("room_history", {}).items():
        # Find the tenant_id for this room
        tid = _LEGACY_TENANT
        for (t, r), _ in backends.rooms._rooms.items():
            if r == rid:
                tid = t
                break
        backends.room_history._history[(tid, rid)] = msgs
    # Room webhooks
    for rid, hooks in data.get("room_webhooks", {}).items():
        tid = _LEGACY_TENANT
        for (t, r), _ in backends.rooms._rooms.items():
            if r == rid:
                tid = t
                break
        backends.webhooks._room_hooks[(tid, rid)] = hooks
    # Presence
    for key, pdata in data.get("presence", {}).items():
        parts = key.split(":", 1)
        tid = parts[0] if len(parts) == 2 else _LEGACY_TENANT
        name = parts[1] if len(parts) == 2 else parts[0]
        backends.presence._entries[(tid, name)] = pdata
    # DM webhooks
    for name, url in data.get("webhooks", {}).items():
        backends.webhooks._dm_hooks[(_LEGACY_TENANT, name)] = url


def _save_to_file():
    data = _snapshot_state()
    encoded = json.dumps(data, indent=2).encode("utf-8")
    _write_atomic(MESSAGES_FILE, encoded)


_persistence_lock = asyncio.Lock()


async def _persist_state():
    async with _persistence_lock:
        await asyncio.to_thread(_save_to_file)


def _load_from_file():
    if not os.path.exists(MESSAGES_FILE):
        return
    try:
        with open(MESSAGES_FILE) as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError, OSError):
        logger.warning("Corrupt persistence file %s, starting fresh", MESSAGES_FILE)
        return
    _apply_loaded_state(data)
    logger.info("Loaded state from %s", MESSAGES_FILE)


async def _run_migrations():
    """Run Alembic migrations to head (auto-upgrade on startup)."""
    try:
        from alembic import command
        from alembic.config import Config

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", "murmur/migrations")
        alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL)

        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
        logger.info("Alembic migrations applied successfully")
    except Exception:
        logger.error("Failed to run migrations", exc_info=True)
        raise


# ---------------------------------------------------------------------------
# Service initialization
# ---------------------------------------------------------------------------


def _init_services(app_instance, redis_conn=None):
    """Create backends and services, store on app.state.

    Called by the lifespan on startup and by reset_state() in tests.
    When *redis_conn* is provided, Redis-backed backends are used;
    otherwise in-memory backends are created.
    """
    from murmur.services.analytics_svc import AnalyticsService
    from murmur.services.invite_svc import InviteService
    from murmur.services.message_svc import MessageService
    from murmur.services.notification_svc import NotificationService
    from murmur.services.presence_svc import PresenceService
    from murmur.services.rate_limit_svc import RateLimitService
    from murmur.services.room_msg_svc import RoomMessageService
    from murmur.services.room_svc import RoomService
    from murmur.services.sse_svc import SSEService
    from murmur.services.webhook_svc import WebhookService

    max_room_history = int(os.environ.get("MAX_ROOM_HISTORY", "200"))
    rate_limit_window = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))
    rate_limit_max = int(os.environ.get("RATE_LIMIT_MAX", "60"))

    if redis_conn is not None:
        from murmur.backends.redis_backends import RedisBackends

        backends = RedisBackends.create(
            redis_conn, max_room_history=max_room_history
        )
    else:
        from murmur.backends.memory import InMemoryBackends

        backends = InMemoryBackends.create(max_room_history=max_room_history)

    # Notification bus — Redis-backed when available, local-only otherwise
    notification = NotificationService(redis_conn=redis_conn)

    rate_limit = RateLimitService(backends.rate_limit, rate_limit_window, rate_limit_max)
    analytics = AnalyticsService(backends.analytics)
    sse = SSEService(backends.sse_tokens, notification=notification)
    # Use durable webhook queue when available (Redis backends have webhook_queue)
    webhook_queue = getattr(backends, "webhook_queue", None)
    webhook = WebhookService(backends.webhooks, queue_backend=webhook_queue)
    message = MessageService(
        backends.messages, sse, webhook, analytics, rate_limit,
        notification=notification,
    )
    room = RoomService(backends.rooms)
    room_msg = RoomMessageService(
        room, backends.room_history, backends.messages,
        sse, webhook, analytics, rate_limit,
        on_enqueue=message.notify_new_message,
    )
    presence = PresenceService(backends.presence)

    invite_secret = os.environ.get("INVITE_SECRET", "") or JWT_SECRET or RELAY_SECRET
    if DATABASE_URL and (not invite_secret or len(invite_secret) < 32):
        raise SystemExit(
            "INVITE_SECRET (or JWT_SECRET) must be set to a strong secret "
            "(min 32 chars) in production mode."
        )
    invite_ttl = int(os.environ.get("INVITE_TTL", str(24 * 60 * 60)))
    invite = InviteService(
        secret=invite_secret or "dev-only-insecure-secret",
        default_ttl=invite_ttl,
    )

    app_instance.state.notification_service = notification
    app_instance.state.message_service = message
    app_instance.state.room_service = room
    app_instance.state.room_msg_service = room_msg
    app_instance.state.presence_service = presence
    app_instance.state.webhook_service = webhook
    app_instance.state.sse_service = sse
    app_instance.state.analytics_service = analytics
    app_instance.state.rate_limit_service = rate_limit
    app_instance.state.invite_service = invite
    app_instance.state.backends = backends


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app):
    logger.info("Relay server starting up")

    # Initialize Postgres if configured
    if DATABASE_URL:
        from murmur.storage.postgres import init_engine
        await init_engine(DATABASE_URL)
        await _run_migrations()
        logger.info("Postgres initialized")

    # Initialize Redis if configured
    redis_conn = None
    redis_url = os.environ.get("REDIS_URL", "")
    if redis_url:
        from murmur.backends.redis_client import init_redis
        redis_conn = await init_redis(redis_url)
        logger.info("Redis initialized")

    # Create backends and services
    _init_services(app, redis_conn=redis_conn)

    # Start cross-replica notification listener
    if hasattr(app.state, "notification_service"):
        await app.state.notification_service.start()

    # Start webhook delivery worker
    if hasattr(app.state, "webhook_service"):
        app.state.webhook_service.start()

    yield

    logger.info("Relay server shutting down")

    # Stop notification listener
    if hasattr(app.state, "notification_service"):
        await app.state.notification_service.stop()

    # Close Redis if it was initialized
    if redis_url:
        from murmur.backends.redis_client import close_redis
        await close_redis()

    # Close Postgres if it was initialized
    if DATABASE_URL:
        from murmur.storage.postgres import close_engine
        await close_engine()

    # Close webhook HTTP client
    if hasattr(app.state, "webhook_service"):
        await app.state.webhook_service.close()


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Murmur Relay",
    description=(
        "Real-time messaging relay for AI agent coordination. "
        "Any agent that speaks HTTP can join rooms, send messages, "
        "and coordinate work through this API."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

# Initialize services eagerly so tests (which skip the lifespan) have
# access to app.state.backends etc. from the moment the module is imported.
_init_services(app)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Inject request_id, security headers, and structlog context."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        if "text/html" in response.headers.get("content-type", ""):
            response.headers["content-security-policy"] = (
                "default-src 'self'; "
                "script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'"
            )
        return response


app.add_middleware(RequestContextMiddleware)

# Instrument Prometheus metrics but don't auto-expose — we add an auth-protected route.
_instrumentator = Instrumentator()
_instrumentator.instrument(app)


@app.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    """Prometheus metrics — requires valid auth."""
    from murmur.auth.middleware import verify_auth

    await verify_auth(request)

    from prometheus_client import generate_latest
    from starlette.responses import Response

    return Response(content=generate_latest(), media_type="text/plain")

# CORS
_cors_origins = os.environ.get("CORS_ORIGINS", "")
if _cors_origins:
    from starlette.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins.split(",")],
        allow_methods=["GET", "POST", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "Bootstrap-Secret"],
    )

# ---------------------------------------------------------------------------
# Wire up routers
# ---------------------------------------------------------------------------

from murmur.admin.routes import router as admin_router  # noqa: E402
from murmur.auth.routes import router as auth_router  # noqa: E402
from murmur.dashboard import router as dashboard_router  # noqa: E402
from murmur.routes import router as relay_router  # noqa: E402

app.include_router(relay_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(dashboard_router)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the relay server as a CLI entrypoint."""
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)


# ---------------------------------------------------------------------------
# Backward-compatible re-exports (used by existing tests)
# ---------------------------------------------------------------------------

from murmur.relay_routes import (  # noqa: E402, I001
    MAX_MESSAGE_SIZE as MAX_MESSAGE_SIZE,  # noqa: F401
    MAX_MESSAGES as MAX_MESSAGES,  # noqa: F401
    RATE_LIMIT_MAX as RATE_LIMIT_MAX,  # noqa: F401
    RATE_LIMIT_WINDOW as RATE_LIMIT_WINDOW,  # noqa: F401
    reset_state as _reset_state,  # noqa: F401
)
from murmur.routes.sse import stream_messages as stream_messages  # noqa: E402, F401

if __name__ == "__main__":
    main()
