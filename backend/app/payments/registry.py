from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import Clock
from app.config import Config
from app.payments.base import PaymentProvider
from app.payments.mock import MockProvider


class UnknownProvider(KeyError):
    pass


class PaymentRegistry:
    def __init__(self, default: str, providers: dict[str, PaymentProvider]) -> None:
        if default not in providers:
            raise UnknownProvider(default)
        self.default_name = default
        self._providers = providers

    @property
    def default(self) -> PaymentProvider:
        return self._providers[self.default_name]

    def get(self, name: str) -> PaymentProvider:
        try:
            return self._providers[name]
        except KeyError:
            raise UnknownProvider(name) from None

    def names(self) -> list[str]:
        return list(self._providers)


def build_registry(config: Config, db: async_sessionmaker[AsyncSession], clock: Clock) -> PaymentRegistry:
    providers: dict[str, PaymentProvider] = {}
    if config.mock_provider_secret:
        providers["mock"] = MockProvider(config.mock_provider_secret, db, clock)
    # Real providers are registered here, e.g.:
    # if config.payment_provider == "acme":
    #     providers["acme"] = AcmeProvider(config.acme_api_url, config.acme_api_key, http)
    return PaymentRegistry(config.payment_provider, providers)
