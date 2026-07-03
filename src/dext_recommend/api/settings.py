"""HTTP/application-state settings for R7b-lite."""
from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEXT_APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://dext:dext_dev_password@127.0.0.1:5432/dext_app"
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_timeout: float = Field(default=30.0, gt=0.0)
    database_statement_timeout: float = Field(default=30.0, gt=0.0)

    http_host: str = "127.0.0.1"
    http_port: int = Field(default=21530, ge=1, le=65535)
    request_body_max_bytes: int = Field(default=1_000_000, ge=1024)
    shutdown_grace_seconds: float = Field(default=10.0, ge=0.1)

    anonymous_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 180, ge=60)
    auth_token_pepper: SecretStr = Field(default_factory=lambda: SecretStr("dev-only-change-me"))
    cookie_secure: bool = False
    cookie_domain: str | None = None
    cookie_samesite: str = "Lax"
    cors_allowed_origins: tuple[str, ...] = ()
    csrf_allowed_origins: tuple[str, ...] = ()
    diagnostics_enabled: bool = False
    sse_heartbeat_seconds: float = Field(default=15.0, gt=0.0)
    idempotency_ttl_seconds: int = Field(default=60 * 60 * 24, ge=60)
    schema_bootstrap: bool = False

    @field_validator("cors_allowed_origins", "csrf_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(value)

    @field_validator("cookie_samesite")
    @classmethod
    def _validate_samesite(cls, value: str) -> str:
        if value not in {"Strict", "Lax", "None"}:
            raise ValueError("cookie_samesite must be Strict, Lax, or None")
        return value

    @property
    def cookie_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "httponly": True,
            "secure": self.cookie_secure,
            "samesite": self.cookie_samesite,
            "path": "/api/v1",
            "max_age": self.anonymous_token_ttl_seconds,
        }
        if self.cookie_domain:
            kwargs["domain"] = self.cookie_domain
        return kwargs

    def safe_snapshot(self) -> dict[str, object]:
        data = self.model_dump(mode="json", exclude={"auth_token_pepper"})
        if "@" in self.database_url:
            scheme, rest = self.database_url.split("://", 1)
            data["database_url"] = scheme + "://***@" + rest.split("@", 1)[1]
        return data


__all__ = ["AppSettings"]
