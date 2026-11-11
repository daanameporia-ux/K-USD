"""Application assembly."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from app.api import admin, auth, client, errors, health, payments_callback, security, telegram_webhook
from app.config import Config, get_config
from app.container import Container
from app.logging import setup_logging


class SpaStaticFiles(StaticFiles):
    """Serve index.html for unknown paths inside a single-page app."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code == 404 and "." not in Path(path).name:
                return await super().get_response("index.html", scope)
            raise


def _mount_frontend(app: FastAPI, base: str) -> None:
    root = Path(base)
    for name in ("app", "admin"):
        directory = root / name
        if (directory / "index.html").exists():
            app.mount(f"/{name}", SpaStaticFiles(directory=directory, html=True), name=f"spa-{name}")

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse("/app/")

    favicon = root / "favicon.ico"
    if favicon.exists():

        @app.get("/favicon.ico", include_in_schema=False)
        async def _favicon() -> FileResponse:
            return FileResponse(favicon)


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
    errors.install(app)
    security.install(app)
    for module in (health, telegram_webhook, payments_callback, auth, client, admin):
        app.include_router(module.router)
    if cfg.frontend_dir:
        _mount_frontend(app, cfg.frontend_dir)
    return app


def build() -> FastAPI:
    setup_logging()
    return create_app()
