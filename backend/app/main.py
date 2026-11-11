"""Application assembly."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.config import Config, get_config
from app.logging import setup_logging


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(title="K-USD", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = cfg
    app.include_router(health.router)
    return app


def build() -> FastAPI:
    setup_logging()
    return create_app()
