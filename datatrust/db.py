"""PostgreSQL access helpers.

All SQL goes through these helpers so that every query is parameterised, connections
carry the metadata schema on their ``search_path`` and connectivity failures surface as
:class:`DatabaseUnavailableError` instead of raw driver exceptions.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from datatrust.config import Settings, get_settings
from datatrust.errors import DatabaseUnavailableError, MetadataNotInitializedError

logger = logging.getLogger(__name__)

Params = Mapping[str, Any] | Sequence[Any] | None
Query = str | sql.Composable


def json_default(value: Any) -> Any:
    """JSON encoder fallback for values returned by PostgreSQL."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def to_jsonb(value: Any) -> Jsonb:
    """Wrap a Python value for a jsonb parameter, normalising Decimals and dates."""
    return Jsonb(json.loads(json.dumps(value, default=json_default)))


class Database:
    """Thin wrapper around psycopg connections bound to one configuration."""

    def __init__(self, settings: Settings | None = None, metadata_schema: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.metadata_schema = metadata_schema or self.settings.metadata_schema

    @contextmanager
    def connect(self, autocommit: bool = False) -> Iterator[psycopg.Connection]:
        """Open a connection whose search_path starts with the metadata schema."""
        try:
            conn = psycopg.connect(
                self.settings.conninfo(),
                autocommit=autocommit,
                row_factory=dict_row,
                options=f"-c search_path={self.metadata_schema},public",
            )
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailableError(
                f"Cannot connect to PostgreSQL at {self.settings.db_host}:{self.settings.db_port}/"
                f"{self.settings.db_name} as {self.settings.db_user}. Is the database running? ({exc})"
            ) from exc
        try:
            yield conn
            if not autocommit:
                conn.commit()
        except Exception:
            if not autocommit and not conn.closed:
                conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Cursor]:
        """Cursor inside a single transaction that commits on success."""
        with self.connect() as conn, conn.cursor() as cur:
            yield cur

    def fetch_all(self, query: Query, params: Params = None) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            return list(cur.fetchall()) if cur.description else []

    def fetch_one(self, query: Query, params: Params = None) -> dict[str, Any] | None:
        rows = self.fetch_all(query, params)
        return rows[0] if rows else None

    def fetch_value(self, query: Query, params: Params = None) -> Any:
        row = self.fetch_one(query, params)
        return next(iter(row.values())) if row else None

    def execute(self, query: Query, params: Params = None) -> int:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            return cur.rowcount

    def is_available(self) -> bool:
        try:
            self.fetch_value("SELECT 1")
            return True
        except DatabaseUnavailableError:
            return False

    def table_exists(self, table: str, schema: str | None = None) -> bool:
        return bool(
            self.fetch_value(
                "SELECT to_regclass(%s) IS NOT NULL AS present",
                (f"{schema or self.metadata_schema}.{table}",),
            )
        )

    def require_metadata(self, *tables: str) -> None:
        """Raise MetadataNotInitializedError unless every table exists."""
        missing = [t for t in tables if not self.table_exists(t)]
        if missing:
            raise MetadataNotInitializedError(
                f"Metadata tables missing in schema '{self.metadata_schema}': {', '.join(missing)}. "
                "Run `make init-db` (or `datatrust init-db`)."
            )


def ensure_database_exists(settings: Settings | None = None) -> bool:
    """Create the configured database if it is missing. Returns True when created.

    Uses the ``postgres`` maintenance database; silently does nothing when the role lacks
    CREATEDB (e.g. the docker-compose database already exists).
    """
    settings = settings or get_settings()
    try:
        with psycopg.connect(settings.conninfo(), autocommit=True):
            return False
    except psycopg.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise DatabaseUnavailableError(f"Cannot connect to PostgreSQL: {exc}") from exc
    try:
        with psycopg.connect(settings.conninfo(dbname="postgres"), autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(settings.db_name)))
            logger.info("Created database %s", settings.db_name)
            return True
    except psycopg.Error as exc:
        raise DatabaseUnavailableError(f"Database {settings.db_name} is missing and could not be created: {exc}") from exc
