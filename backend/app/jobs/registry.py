from __future__ import annotations

from typing import TYPE_CHECKING

from app.jobs.outbox import send_notifications
from app.jobs.rate import rate_interval, refresh_rate
from app.jobs.scheduler import Job, Scheduler
from app.services import orders, withdrawals

if TYPE_CHECKING:
    from app.container import Container


def build_scheduler(c: Container) -> Scheduler:
    jobs = [
        Job("refresh_rate", refresh_rate, rate_interval),
        Job("send_notifications", send_notifications, 2),
        Job("expire_orders", orders.expire_due, 20),
        Job("fail_stuck_creating", orders.fail_stuck_creating, 60),
        Job("verify_withdrawals", withdrawals.verify_due, 30),
    ]
    return Scheduler(c, jobs)
