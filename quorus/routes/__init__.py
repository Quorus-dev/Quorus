"""Route sub-package — combines all domain route files into a single router."""

from fastapi import APIRouter

from quorus.routes.agents import router as agents_router
from quorus.routes.analytics import router as analytics_router
from quorus.routes.audit import router as audit_router
from quorus.routes.health import router as health_router
from quorus.routes.invites import router as invites_router
from quorus.routes.messages import router as messages_router
from quorus.routes.presence import router as presence_router
from quorus.routes.room_messages import router as room_messages_router
from quorus.routes.room_state import router as room_state_router
from quorus.routes.rooms import router as rooms_router
from quorus.routes.sse import router as sse_router
from quorus.routes.usage import router as usage_router
from quorus.routes.webhooks import router as webhooks_router

router = APIRouter()
router.include_router(health_router)
router.include_router(messages_router)
router.include_router(rooms_router)
router.include_router(room_messages_router)
router.include_router(room_state_router)
router.include_router(presence_router)
router.include_router(webhooks_router)
router.include_router(sse_router)
router.include_router(analytics_router)
router.include_router(agents_router)
router.include_router(invites_router)
router.include_router(usage_router)
router.include_router(audit_router)
