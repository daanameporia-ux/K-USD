"""Bot handlers assembly."""

from __future__ import annotations

from app.telegram.handlers import common
from app.telegram.router import Router


def build_router() -> Router:
    router = Router()
    router.include(common.router)
    return router
