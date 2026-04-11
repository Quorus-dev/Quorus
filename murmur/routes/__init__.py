"""Route sub-package — combines all domain route files into a single router."""

from fastapi import APIRouter

from murmur.routes.agents import router as agents_router
from murmur.routes.analytics import router as analytics_router
from murmur.routes.health import router as health_router
from murmur.routes.invites import router as invites_router
from murmur.routes.messages import router as messages_router
from murmur.routes.presence import router as presence_router
from murmur.routes.room_messages import router as room_messages_router
from murmur.routes.rooms import router as rooms_router
from murmur.routes.sse import router as sse_router
from murmur.routes.webhooks import router as webhooks_router

router = APIRouter()
router.include_router(health_router)
router.include_router(messages_router)
router.include_router(rooms_router)
router.include_router(room_messages_router)
router.include_router(presence_router)
router.include_router(webhooks_router)
router.include_router(sse_router)
router.include_router(analytics_router)
router.include_router(agents_router)
router.include_router(invites_router)
