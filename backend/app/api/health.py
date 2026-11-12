from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    container = getattr(request.app.state, "container", None)
    if container is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    checks = await container.readiness()
    ok = all(checks.values())
    body = {"status": "ok" if ok else "fail", "checks": checks, "bot_configured": container.bot_configured}
    return JSONResponse(body, status_code=200 if ok else 503)
