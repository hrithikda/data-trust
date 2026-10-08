"""Persist generated data: CSV snapshot on disk and bulk load into the raw schema."""

from __future__ import annotations

import csv
import json
import logging
from importlib import resources
from pathlib import Path

from psycopg import sql

from datatrust.db import Database, json_default
from datatrust.generator.generate import GeneratedDataset

logger = logging.getLogger(__name__)


def _columns(rows: list[dict]) -> list[str]:
    columns: list[str] = []
    for row in rows[:1]:
        columns = list(row.keys())
    return columns


def write_snapshot(dataset: GeneratedDataset, directory: Path) -> Path:
    """Write one CSV per raw table plus ``defect_manifest.json`` (the ground truth)."""
    directory.mkdir(parents=True, exist_ok=True)
    for table, rows in dataset.tables.items():
        with (directory / f"{table}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=_columns(rows))
            writer.writeheader()
            writer.writerows(rows)
    manifest = {
        "seed": dataset.seed,
        "scale": dataset.scale,
        "reference_date": dataset.reference_date.isoformat(),
        "row_counts": dataset.row_counts(),
        "defects": dataset.defects,
    }
    path = directory / "defect_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, default=json_default), encoding="utf-8")
    logger.info("Wrote snapshot to %s", directory)
    return path


def load_raw(dataset: GeneratedDataset, db: Database, raw_schema: str) -> dict[str, int]:
    """Recreate the raw tables and COPY every generated row into them. Safe to rerun."""
    ddl = resources.files("datatrust.sql").joinpath("raw_schema.sql").read_text(encoding="utf-8")
    loaded: dict[str, int] = {}
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(raw_schema)))
        cur.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(raw_schema)))
        cur.execute(ddl)
        for table, rows in dataset.tables.items():
            columns = _columns(rows)
            if not columns:
                loaded[table] = 0
                continue
            copy_stmt = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
                sql.Identifier(raw_schema), sql.Identifier(table),
                sql.SQL(", ").join(sql.Identifier(c) for c in columns),
            )
            with cur.copy(copy_stmt) as copy:
                for row in rows:
                    copy.write_row([row[c] for c in columns])
            loaded[table] = len(rows)
        cur.execute(sql.SQL("ANALYZE"))
    logger.info("Loaded raw tables: %s", loaded)
    return loaded
