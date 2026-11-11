"""Minimal asyncio scheduler living in the app process.

Each job: wrapped in try/except, guarded by pg_try_advisory_lock, records its last successful run.
"""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.logging import get_logger
from app.services import notify

if TYPE_CHECKING:
    from app.container import Container

log = get_logger(__name__)

JobFunc = Callable[["Container"], Awaitable[object]]
IntervalFunc = Callable[["Container"], Awaitable[float]]


@dataclass
class Job:
    name: str
    func: JobFunc
    interval: float | IntervalFunc
    lock_key: int = 0

    def __post_init__(self) -> None:
        if not self.lock_key:
            self.lock_key = zlib.crc32(f"kusd-job:{self.name}".encode())


@dataclass
class JobState:
    last_success: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    runs: int = 0
    skipped: int = 0


@dataclass
class Scheduler:
    c: Container
    jobs: list[Job]
    state: dict[str, JobState] = field(default_factory=dict)
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        for job in self.jobs:
            self.state.setdefault(job.name, JobState())

    def get(self, name: str) -> Job:
        return next(j for j in self.jobs if j.name == name)

    async def run_once(self, job: Job | str) -> bool:
        """Run a job once if its advisory lock is free. Returns True if it ran successfully."""
        if isinstance(job, str):
            job = self.get(job)
        st = self.state[job.name]
        async with self.c.engine.connect() as conn:
            got = await conn.scalar(select(func.pg_try_advisory_lock(job.lock_key)))
            await conn.commit()
            if not got:
                st.skipped += 1
                return False
            try:
                await job.func(self.c)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                now = self.c.clock.now()
                st.last_error = f"{type(exc).__name__}: {exc}"[:500]
                st.last_error_at = now
                log.exception("job_failed", job=job.name)
                await self._alert_failure(job.name, st.last_error)
                return False
            else:
                st.last_success = self.c.clock.now()
                st.runs += 1
                return True
            finally:
                await conn.scalar(select(func.pg_advisory_unlock(job.lock_key)))
                await conn.commit()

    async def _alert_failure(self, name: str, error: str) -> None:
        now = self.c.clock.now()
        if not self.c.throttle.allow(f"job:{name}", now):
            return
        try:
            async with self.c.db.begin() as session:
                await notify.alert(session, f"❗ Фоновая задача <b>{name}</b> упала: {error[:200]}", now=now)
        except Exception:
            log.exception("job_alert_failed", job=name)

    async def _interval(self, job: Job) -> float:
        if callable(job.interval):
            try:
                return float(await job.interval(self.c))
            except Exception:
                log.exception("job_interval_failed", job=job.name)
                return 60.0
        return float(job.interval)

    async def _loop(self, job: Job) -> None:
        while not self._stop.is_set():
            await self.run_once(job)
            delay = await self._interval(job)
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)

    def start(self) -> None:
        self._stop.clear()
        self._tasks = [asyncio.create_task(self._loop(job), name=f"job:{job.name}") for job in self.jobs]

    async def stop(self, timeout: float = 20.0) -> None:
        self._stop.set()
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=timeout)
            for task in pending:
                task.cancel()
        self._tasks = []

    @property
    def running(self) -> bool:
        return bool(self._tasks) and all(not t.done() for t in self._tasks)
