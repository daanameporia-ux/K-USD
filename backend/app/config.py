"""Environment configuration (secrets and infrastructure)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_RATE_URL = "https://api.rapira.net/open/market/rates"
DEFAULT_BSC_RPCS = "https://bsc-dataseed.binance.org,https://bsc-dataseed1.defibit.io"


class ConfigError(RuntimeError):
    """Raised when configuration is invalid; the app refuses to start."""


def _split_csv(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).split(",") if p.strip()]


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # defaults to prod so that a forgotten ENV never relaxes production checks
    env: Literal["prod", "dev", "test"] = Field("prod", alias="ENV")
    bot_token: str = Field(alias="BOT_TOKEN", min_length=10)
    bot_mode: Literal["webhook", "polling"] = Field("webhook", alias="BOT_MODE")
    public_base_url: str = Field(alias="PUBLIC_BASE_URL")
    telegram_webhook_secret: str = Field(alias="TELEGRAM_WEBHOOK_SECRET", min_length=16)
    telegram_api_url: str = Field("https://api.telegram.org", alias="TELEGRAM_API_URL")
    database_url: str = Field(alias="DATABASE_URL")
    session_secret: str = Field(alias="SESSION_SECRET", min_length=32)
    superadmin_tg_ids: Annotated[list[int], NoDecode] = Field(default_factory=list, alias="SUPERADMIN_TG_IDS")
    staff_chat_id: int | None = Field(None, alias="STAFF_CHAT_ID")
    payment_provider: str = Field("mock", alias="PAYMENT_PROVIDER")
    mock_provider_secret: str = Field("", alias="MOCK_PROVIDER_SECRET")
    allow_mock_in_prod: bool = Field(False, alias="ALLOW_MOCK_IN_PROD")
    rate_source_url: str = Field(DEFAULT_RATE_URL, alias="RATE_SOURCE_URL")
    tron_api_url: str = Field("https://api.trongrid.io", alias="TRON_API_URL")
    tron_api_key: str | None = Field(None, alias="TRON_API_KEY")
    bsc_rpc_urls: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: _split_csv(DEFAULT_BSC_RPCS), alias="BSC_RPC_URLS"
    )
    domain: str | None = Field(None, alias="DOMAIN")
    acme_email: str | None = Field(None, alias="ACME_EMAIL")
    frontend_dir: str | None = Field(None, alias="FRONTEND_DIR")
    run_scheduler: bool = Field(True, alias="RUN_SCHEDULER")
    # Directory with the dumps of the `backup` container (mounted read-only): its state is watched and alerted on.
    backup_dir: str | None = Field(None, alias="BACKUP_DIR")

    @field_validator("superadmin_tg_ids", mode="before")
    @classmethod
    def _parse_ids(cls, v: object) -> list[int]:
        return [int(x) for x in _split_csv(v)]

    @field_validator("bsc_rpc_urls", mode="before")
    @classmethod
    def _parse_rpcs(cls, v: object) -> list[str]:
        items = _split_csv(v)
        return items or _split_csv(DEFAULT_BSC_RPCS)

    @field_validator("staff_chat_id", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if v in ("", None) else v

    @field_validator("tron_api_key", "domain", "acme_email", "frontend_dir", "backup_dir", mode="before")
    @classmethod
    def _empty_str_none(cls, v: object) -> object:
        return None if v == "" else v

    @field_validator("public_base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        v = v.rstrip("/")
        if not v.startswith(("https://", "http://")):
            raise ValueError("PUBLIC_BASE_URL должен начинаться с https://")
        return v

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        if v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v.removeprefix("postgresql://")
        if v.startswith("postgres://"):
            v = "postgresql+asyncpg://" + v.removeprefix("postgres://")
        return v

    @model_validator(mode="after")
    def _check_consistency(self) -> Config:
        if self.env == "prod" and self.payment_provider == "mock" and not self.allow_mock_in_prod:
            raise ValueError(
                "PAYMENT_PROVIDER=mock запрещён в ENV=prod (выставьте ALLOW_MOCK_IN_PROD=true, если это осознанно)"
            )
        if self.payment_provider == "mock" and len(self.mock_provider_secret) < 16:
            raise ValueError("MOCK_PROVIDER_SECRET обязателен для mock-провайдера (минимум 16 символов)")
        if self.env == "prod" and not self.public_base_url.startswith("https://"):
            raise ValueError("В ENV=prod PUBLIC_BASE_URL должен быть https://")
        return self


def load_config(**overrides: object) -> Config:
    try:
        return Config(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "config"
            lines.append(f"  - {loc}: {err['msg']}")
        raise ConfigError("Некорректная конфигурация:\n" + "\n".join(lines)) from exc


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
