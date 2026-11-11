"""Bot handlers assembly."""

from __future__ import annotations

from app.telegram.handlers import balance, buy, common
from app.telegram.router import Router


def build_router() -> Router:
    router = Router()
    router.include(common.router)
    router.include(balance.router)
    router.include(buy.router)
    return router
