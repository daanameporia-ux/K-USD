from __future__ import annotations

from typing import TYPE_CHECKING

from app.jobs.outbox import send_notifications
from app.jobs.rate import rate_interval, refresh_rate
from app.jobs.scheduler import Job, Scheduler

if TYPE_CHECKING:
    from app.container import Container


def build_scheduler(c: Container) -> Scheduler:
    jobs = [
        Job("refresh_rate", refresh_rate, rate_interval),
        Job("send_notifications", send_notifications, 2),
    ]
    return Scheduler(c, jobs)
