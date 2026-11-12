from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from app.domain.rates import RateParseError, parse_rapira
from app.domain.text import escape_html
from app.logging import get_logger
from app.services import notify, rates
from app.services import settings as settings_svc

if TYPE_CHECKING:
    from app.container import Container

log = get_logger(__name__)


class RateFetchError(Exception):
    pass


async def rate_interval(c: Container) -> float:
    async with c.db() as session:
        return float((await settings_svc.load(session)).rate_ttl_seconds)


async def refresh_rate(c: Container) -> None:
    async with c.db() as session:
        biz = await settings_svc.load(session)
    now = c.clock.now()
    parsed = None
    try:
        resp = await c.http.get(c.config.rate_source_url, timeout=httpx.Timeout(10.0, connect=5.0))
        if resp.status_code != 200:
            raise RateFetchError(f"HTTP {resp.status_code}")
        parsed = parse_rapira(resp.content, biz.rate_field)
    except (httpx.HTTPError, RateFetchError, RateParseError) as exc:
        log.warning("rate_fetch_failed", error=f"{type(exc).__name__}: {exc}")
        if isinstance(exc, RateParseError) and c.throttle.allow("rate_parse", now):
            async with c.db.begin() as session:
                await notify.alert(session, f"⚠️ Ответ Rapira не разобран: {escape_html(exc)}", now=now)

    async with c.db.begin() as session:
        if parsed is not None:
            result = await rates.store(session, parsed, now=now, guard_pct=biz.rate_jump_guard_pct)
            if not result.rate.accepted:
                log.warning("rate_jump_rejected", value=str(parsed.value), jump_pct=str(result.jump_pct))
                if c.throttle.allow("rate_jump", now):
                    prev = await rates.latest_accepted(session)
                    await notify.alert(
                        session,
                        f"⚠️ Скачок курса на {result.jump_pct:.2f}%: {prev.value if prev else '—'} → {parsed.value}. "
                        "Курс не применён. Подтвердите его в админке или задайте ручной курс.",
                        now=now,
                    )
        # not more often than once per 10 minutes, even if the source flaps between valid and invalid
        if await rates.current(session, biz, now) is None and c.throttle.allow("rate_unavailable", now):
            await notify.alert(session, "❗ Нет валидного курса — покупки недоступны.", now=now)
