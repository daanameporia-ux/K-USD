from __future__ import annotations

from app.domain.enums import KycStatus
from app.kyc.base import KycInput, KycStep
from app.models import User


class StubKycProvider:
    """Registration stub: every user passes immediately."""

    async def start(self, user: User) -> KycStep:
        return KycStep(status="passed")

    async def handle_input(self, user: User, update: KycInput) -> KycStep:
        return KycStep(status="passed")

    async def is_passed(self, user: User) -> bool:
        return user.kyc_status == KycStatus.passed
