"""Create (or upgrade in place) the DataTrust metadata schema."""

from __future__ import annotations

import logging
from importlib import resources

from psycopg import sql

from datatrust.db import Database, ensure_database_exists

logger = logging.getLogger(__name__)


def initialize_metadata_schema(db: Database, create_database: bool = True, reset: bool = False) -> None:
    """Create the database (if permitted), the metadata schema and all tables. Idempotent.

    ``reset`` drops the metadata schema first, discarding all DataTrust history.
    """
    if create_database:
        ensure_database_exists(db.settings)
    ddl = resources.files("datatrust.sql").joinpath("metadata_schema.sql").read_text(encoding="utf-8")
    with db.connect() as conn, conn.cursor() as cur:
        if reset:
            cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(db.metadata_schema)))
            logger.warning("Dropped metadata schema '%s'", db.metadata_schema)
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(db.metadata_schema)))
        cur.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(db.metadata_schema)))
        cur.execute(ddl)
    logger.info("Metadata schema '%s' is ready", db.metadata_schema)


def record_pipeline_event(db: Database, step: str, status: str, details: dict | None = None) -> None:
    """Append a pipeline bookkeeping event (used by the UI to explain platform state)."""
    from datatrust.db import to_jsonb

    db.execute(
        "INSERT INTO pipeline_events (step, status, details) VALUES (%s, %s, %s)",
        (step, status, to_jsonb(details or {})),
    )
