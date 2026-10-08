"""Isolated schema where evaluation cases are loaded one at a time.

Tables mirror the staging models' dbt contracts (column names and types from the manifest),
so rules run against the cases exactly as they run against the warehouse.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from psycopg import sql

from datatrust.db import Database
from datatrust.errors import ConfigurationError
from datatrust.evaluation.cases import Records
from datatrust.metadata.dbt_artifacts import parse_artifacts

logger = logging.getLogger(__name__)


class Sandbox:
    def __init__(self, db: Database, schema: str, target_dir: Path) -> None:
        self.db = db
        self.schema = schema
        project = parse_artifacts(target_dir)
        self.tables: dict[str, list[tuple[str, str]]] = {
            a.name: [(c.name, c.data_type or "text") for c in a.columns]
            for a in project.assets if a.layer == "staging"
        }
        if not self.tables:
            raise ConfigurationError("No staging models in the dbt manifest; cannot build the evaluation sandbox")

    def create(self) -> None:
        """(Re)create the sandbox schema and one empty table per staging model."""
        with self.db.transaction() as cur:
            cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema)))
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
            for table, columns in self.tables.items():
                cur.execute(sql.SQL("CREATE TABLE {} ({})").format(
                    sql.Identifier(self.schema, table),
                    sql.SQL(", ").join(sql.SQL("{} {}").format(sql.Identifier(name), sql.SQL(_safe_type(dtype)))
                                       for name, dtype in columns),
                ))
        logger.info("Evaluation sandbox '%s' created with %d tables", self.schema, len(self.tables))

    def validate(self, records: Records, where: str) -> None:
        for table, rows in records.items():
            if table not in self.tables:
                raise ConfigurationError(f"{where}: unknown table '{table}'")
            known = {name for name, _ in self.tables[table]}
            for row in rows:
                unknown = set(row) - known
                if unknown:
                    raise ConfigurationError(f"{where}: unknown columns {sorted(unknown)} for {table}")

    def load(self, *record_sets: Records) -> None:
        """Truncate every sandbox table and insert the given record sets."""
        with self.db.transaction() as cur:
            cur.execute(sql.SQL("TRUNCATE {}").format(
                sql.SQL(", ").join(sql.Identifier(self.schema, t) for t in self.tables)))
            for records in record_sets:
                for table, rows in records.items():
                    for row in rows:
                        self._insert(cur, table, row)

    def _insert(self, cur: Any, table: str, row: dict[str, Any]) -> None:
        columns = list(row)
        cur.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(self.schema, table),
                sql.SQL(", ").join(sql.Identifier(c) for c in columns),
                sql.SQL(", ").join(sql.Placeholder() for _ in columns),
            ),
            [row[c] for c in columns],
        )


_ALLOWED_TYPES = ("text", "integer", "bigint", "boolean", "date", "timestamp", "numeric", "double precision",
                  "character varying")


def _safe_type(data_type: str) -> str:
    """Only allow plain column types from dbt metadata into DDL."""
    normalised = data_type.strip().lower()
    if not normalised.startswith(_ALLOWED_TYPES) or any(ch in normalised for ch in ";'\"-/"):
        raise ConfigurationError(f"Unsupported column type in dbt metadata: {data_type!r}")
    return normalised
