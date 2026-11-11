"""Application assembly."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health, payments_callback, telegram_webhook
from app.config import Config, get_config
from app.container import Container
from app.logging import setup_logging


def create_app(config: Config | None = None, *, container: Container | None = None) -> FastAPI:
    cfg = config or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        own = app.state.container is None
        if own:
            app.state.container = Container(cfg)
            await app.state.container.startup()
        try:
            yield
        finally:
            if own:
                await app.state.container.shutdown()
            else:
                await app.state.container.dispatcher.drain()

    app = FastAPI(title="K-USD", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = cfg
    app.state.container = container
    app.include_router(health.router)
    app.include_router(telegram_webhook.router)
    app.include_router(payments_callback.router)
    return app


def build() -> FastAPI:
    setup_logging()
    return create_app()
