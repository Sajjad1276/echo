# ================================================================
# ECHO — CONFIGURATION
# FILE: config.py
# ================================================================

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


AppEnvironment = Literal["development", "staging", "production"]

LogLevel = Literal[
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
]


class Settings(BaseSettings):
    """
    Central configuration for the ECHO application.

    Configuration only.
    No game logic, database connection, Telegram bot,
    handlers, routers, or business logic.
    """

    # ------------------------------------------------------------
    # Application
    # ------------------------------------------------------------

    app_name: str = Field(default="ECHO", min_length=1)
    app_env: AppEnvironment = "development"
    debug: bool = False
    log_level: LogLevel = "INFO"

    # ------------------------------------------------------------
    # Core
    # ------------------------------------------------------------

    bot_token: str = Field(..., min_length=1, repr=False)
    database_url: str = Field(..., min_length=1, repr=False)
    redis_url: str = Field(..., min_length=1, repr=False)

    # ------------------------------------------------------------
    # Game
    # ------------------------------------------------------------

    starting_cash: int = Field(default=10_000, ge=0)
    starting_energy: int = Field(default=100, ge=0)

    # ------------------------------------------------------------
    # Session
    # ------------------------------------------------------------

    session_ttl_seconds: int = Field(default=300, ge=1)
    max_active_sessions_per_user: int = Field(default=3, ge=1)
    max_active_sessions_per_city: int = Field(default=100, ge=1)

    # ------------------------------------------------------------
    # Rate Limits
    # ------------------------------------------------------------

    user_rate_limit: int = Field(default=30, ge=1)
    group_rate_limit: int = Field(default=100, ge=1)
    intent_rate_limit: int = Field(default=10, ge=1)

    # ------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------

    redis_prefix: str = Field(default="echo:", min_length=1)

    # ------------------------------------------------------------
    # Pydantic Settings
    # ------------------------------------------------------------

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    # ============================================================
    # Validators
    # ============================================================

    @field_validator("app_name")
    @classmethod
    def validate_app_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("APP_NAME cannot be empty.")

        return value

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("BOT_TOKEN is required.")

        return value

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("DATABASE_URL is required.")

        if value.startswith("postgres://"):
            value = "postgresql+asyncpg://" + value[len("postgres://"):]

        elif value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value[len("postgresql://"):]

        elif value.startswith("postgresql+asyncpg://"):
            pass

        else:
            raise ValueError(
                "DATABASE_URL must use PostgreSQL. "
                "Supported formats are: postgres://, "
                "postgresql://, postgresql+asyncpg://"
            )

        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("REDIS_URL is required.")

        if not value.startswith(("redis://", "rediss://")):
            raise ValueError(
                "REDIS_URL must start with redis:// or rediss://."
            )

        return value

    @field_validator("redis_prefix")
    @classmethod
    def normalize_redis_prefix(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("REDIS_PREFIX cannot be empty.")

        if not value.endswith(":"):
            value += ":"

        return value

    # ============================================================
    # Environment Defaults
    # ============================================================

    @model_validator(mode="before")
    @classmethod
    def set_environment_defaults(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values

        app_env = values.get(
            "APP_ENV",
            values.get("app_env", "development"),
        )

        debug_provided = (
            "DEBUG" in values
            or "debug" in values
        )

        if not debug_provided:
            values["DEBUG"] = app_env != "production"

        return values


settings = Settings()
