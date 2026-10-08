"""Central configuration.

Settings are read from environment variables (prefixed ``DATATRUST_``) and an optional
``.env`` file in the repository root. Nothing secret is hardcoded: the defaults match the
local docker-compose database and are documented in ``.env.example``.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class Settings(BaseSettings):
    """Runtime configuration for every DataTrust component."""

    model_config = SettingsConfigDict(
        env_prefix="DATATRUST_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_host: str = "localhost"
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = "datatrust"
    db_user: str = "datatrust"
    db_password: SecretStr = SecretStr("datatrust")
    db_connect_timeout: int = Field(default=5, ge=1, le=120)

    metadata_schema: str = "datatrust"
    raw_schema: str = "raw"
    eval_schema: str = "datatrust_eval"

    reference_date: date = date(2026, 9, 30)
    seed: int = 20260930
    scale: float = Field(default=1.0, gt=0, le=10)

    ruleset: Literal["baseline", "improved"] = "improved"
    log_level: str = "INFO"

    dbt_project_dir: Path = REPO_ROOT / "dbt"
    config_dir: Path = REPO_ROOT / "config"
    evaluation_dir: Path = REPO_ROOT / "evaluation"
    generated_data_dir: Path = REPO_ROOT / "data" / "generated"

    @field_validator("metadata_schema", "raw_schema", "eval_schema")
    @classmethod
    def _valid_schema(cls, value: str) -> str:
        if not _IDENTIFIER.match(value):
            raise ValueError(f"schema name {value!r} must be a lowercase SQL identifier")
        return value

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("log_level must be DEBUG, INFO, WARNING or ERROR")
        return level

    @property
    def dbt_target_dir(self) -> Path:
        return self.dbt_project_dir / "target"

    @property
    def reference_ts(self) -> datetime:
        """End of the reference day: the moment the synthetic snapshot was taken."""
        return datetime.combine(self.reference_date, time(23, 59, 59))

    def conninfo(self, dbname: str | None = None) -> str:
        """libpq connection string. The password is only materialised here."""
        from psycopg.conninfo import make_conninfo

        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=dbname or self.db_name,
            user=self.db_user,
            password=self.db_password.get_secret_value(),
            connect_timeout=self.db_connect_timeout,
            application_name="datatrust",
        )

    def dbt_env(self) -> dict[str, str]:
        """Environment variables consumed by ``dbt/profiles.yml``."""
        return {
            "DATATRUST_DB_HOST": self.db_host,
            "DATATRUST_DB_PORT": str(self.db_port),
            "DATATRUST_DB_NAME": self.db_name,
            "DATATRUST_DB_USER": self.db_user,
            "DATATRUST_DB_PASSWORD": self.db_password.get_secret_value(),
            "DATATRUST_REFERENCE_DATE": self.reference_date.isoformat(),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings instance."""
    return Settings()
