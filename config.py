"""Environment-backed application settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import os

from dotenv import dotenv_values
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_DIR.parent

# Load repo root first, then package .env — non-empty values win (fixes empty tokenintel/.env shadowing parent keys)
_ENV_FILES: tuple[Path, ...] = (
    _REPO_ROOT / ".env",
    _PACKAGE_DIR / ".env",
)


def _load_env_files() -> None:
    """Merge .env files so a filled parent .env is not blocked by empty local placeholders."""
    merged: dict[str, str] = {}
    for path in _ENV_FILES:
        if not path.is_file():
            continue
        for key, value in dotenv_values(path).items():
            if value is not None and str(value).strip():
                merged[key] = str(value).strip()
    for key, value in merged.items():
        os.environ[key] = value


_load_env_files()


class Settings(BaseSettings):
    """Central configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        # Values come from merged os.environ (_load_env_files); do not re-read empty placeholder .env files.
        extra="ignore",
        case_sensitive=False,
    )

    # LLM
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    groq_api_key: SecretStr | None = Field(default=None, validation_alias="GROQ_API_KEY")
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    tintel_llm_model: str = Field(
        default="gpt-4o",
        validation_alias="TINTEL_LLM_MODEL",
        description="Default LLM for Swarms agents (e.g. gpt-4o, claude-3-5-sonnet-20241022).",
    )

    # Web3 APIs
    birdeye_api_key: SecretStr | None = Field(default=None, validation_alias="BIRDEYE_API_KEY")
    helius_api_key: SecretStr | None = Field(default=None, validation_alias="HELIUS_API_KEY")

    # X / Twitter
    x_bearer_token: SecretStr | None = Field(default=None, validation_alias="X_BEARER_TOKEN")

    # Runtime
    tintel_log_level: Literal["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR"] = Field(
        default="INFO",
        validation_alias="TINTEL_LOG_LEVEL",
    )
    tintel_cache_dir: Path = Field(
        default=Path(".cache/tokenintel"),
        validation_alias="TINTEL_CACHE_DIR",
    )
    tintel_cache_ttl_seconds: int = Field(default=300, ge=60, validation_alias="TINTEL_CACHE_TTL_SECONDS")
    tintel_api_rate_limit_per_minute: int = Field(
        default=30,
        ge=1,
        validation_alias="TINTEL_API_RATE_LIMIT_PER_MINUTE",
    )
    tintel_max_retries: int = Field(default=3, ge=1, le=10, validation_alias="TINTEL_MAX_RETRIES")
    tintel_request_timeout_seconds: int = Field(
        default=30,
        ge=5,
        validation_alias="TINTEL_REQUEST_TIMEOUT_SECONDS",
    )

    @field_validator(
        "openai_api_key",
        "groq_api_key",
        "anthropic_api_key",
        "birdeye_api_key",
        "helius_api_key",
        "x_bearer_token",
        mode="before",
    )
    @classmethod
    def _normalize_secret(cls, value: SecretStr | str | None) -> str | None:
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        cleaned = raw.strip().strip('"').strip("'")
        return cleaned or None

    @field_validator("tintel_cache_dir", mode="before")
    @classmethod
    def _coerce_cache_dir(cls, value: str | Path) -> Path:
        return Path(value)

    @property
    def llm_model(self) -> str:
        """Alias used by agents module."""
        return self.tintel_llm_model

    def has_llm_provider(self) -> bool:
        return any(
            key is not None and key.get_secret_value().strip()
            for key in (self.openai_api_key, self.groq_api_key, self.anthropic_api_key)
        )

    def has_x_api(self) -> bool:
        return bool(self.secret_or_none(self.x_bearer_token))

    def require_llm_provider(self) -> None:
        if not self.has_llm_provider():
            raise ValueError(
                "At least one LLM API key is required: OPENAI_API_KEY, GROQ_API_KEY, or ANTHROPIC_API_KEY."
            )

    def secret_or_none(self, secret: SecretStr | None) -> str | None:
        if secret is None:
            return None
        value = secret.get_secret_value().strip()
        return value or None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    settings = Settings()
    settings.tintel_cache_dir.mkdir(parents=True, exist_ok=True)
    return settings


def reload_settings() -> Settings:
    """Clear settings cache after .env changes (e.g. Streamlit dev reload)."""
    get_settings.cache_clear()
    _load_env_files()
    return get_settings()
