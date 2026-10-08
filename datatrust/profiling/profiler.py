"""Profile every catalogued relation with the same generic SQL.

For each asset the profiler issues one aggregate query (row count, null/distinct counts,
min/max and numeric statistics for every column) plus one small query per column for the
most common values and representative examples. Statistics are chosen by data type, so
no table-specific code exists. Columns flagged as PII keep their counts but never store
values (min/max, common values and examples are withheld).

Freshness is the newest value of the asset's freshness column that is not after the
reference timestamp (future-dated values are a quality defect, not freshness), compared
with the asset's SLA.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg import sql

from datatrust.config import Settings
from datatrust.db import Database, to_jsonb
from datatrust.errors import DataTrustError
from datatrust.metadata.schema import record_pipeline_event

logger = logging.getLogger(__name__)

NUMERIC_PREFIXES = ("smallint", "integer", "bigint", "numeric", "double precision", "real", "decimal")
TEMPORAL_PREFIXES = ("timestamp", "date")


def type_family(data_type: str | None) -> str:
    """Classify a PostgreSQL type into numeric / temporal / boolean / text."""
    dt = (data_type or "").lower()
    if dt.startswith(NUMERIC_PREFIXES):
        return "numeric"
    if dt.startswith(TEMPORAL_PREFIXES):
        return "temporal"
    if dt == "boolean":
        return "boolean"
    return "text"


@dataclass(frozen=True)
class ColumnTarget:
    column_id: int
    name: str
    data_type: str | None
    contains_pii: bool

    @property
    def family(self) -> str:
        return type_family(self.data_type)


@dataclass(frozen=True)
class ProfileTarget:
    asset_id: int
    name: str
    schema_name: str
    relation_name: str
    freshness_column: str | None
    freshness_sla_hours: int | None
    columns: tuple[ColumnTarget, ...]

    @property
    def relation(self) -> sql.Identifier:
        return sql.Identifier(self.schema_name, self.relation_name)


@dataclass
class ColumnProfile:
    column_id: int
    column_name: str
    null_count: int
    null_rate: float
    distinct_count: int
    uniqueness: float
    min_value: str | None = None
    max_value: str | None = None
    mean_value: float | None = None
    stddev_value: float | None = None
    median_value: float | None = None
    top_values: list[dict[str, Any]] = field(default_factory=list)
    sample_values: list[str] = field(default_factory=list)


@dataclass
class AssetProfile:
    asset_id: int
    name: str
    row_count: int
    column_count: int
    freshest_value: datetime | None
    freshness_lag_hours: float | None
    freshness_status: str
    duration_ms: int
    columns: list[ColumnProfile]


@dataclass
class ProfileRunSummary:
    profile_run_id: int
    assets_profiled: int
    columns_profiled: int
    skipped: dict[str, str]
    duration_ms: int


def freshness(freshest: datetime | date | None, reference_ts: datetime, sla_hours: int | None
              ) -> tuple[datetime | None, float | None, str]:
    """Return (freshest timestamp, lag in hours, status) for an asset."""
    if freshest is None:
        return None, None, "unknown"
    if not isinstance(freshest, datetime):
        freshest = datetime.combine(freshest, datetime.min.time())
    lag = round((reference_ts - freshest).total_seconds() / 3600, 2)
    if sla_hours is None:
        return freshest, lag, "unknown"
    return freshest, lag, "fresh" if lag <= sla_hours else "stale"


def compile_stats_query(target: ProfileTarget) -> sql.Composed:
    """One aggregate query computing per-column statistics for an asset."""
    parts: list[sql.Composable] = [sql.SQL("count(*) AS row_count")]
    for i, col in enumerate(target.columns):
        ident = sql.Identifier(col.name)
        parts.append(sql.SQL("count({c}) AS {a}").format(c=ident, a=sql.Identifier(f"nn_{i}")))
        parts.append(sql.SQL("count(DISTINCT {c}) AS {a}").format(c=ident, a=sql.Identifier(f"d_{i}")))
        if col.family in ("numeric", "temporal", "text") and not col.contains_pii:
            parts.append(sql.SQL("min({c})::text AS {a}").format(c=ident, a=sql.Identifier(f"min_{i}")))
            parts.append(sql.SQL("max({c})::text AS {a}").format(c=ident, a=sql.Identifier(f"max_{i}")))
        if col.family == "numeric":
            parts.append(sql.SQL("avg({c})::float8 AS {a}").format(c=ident, a=sql.Identifier(f"mean_{i}")))
            parts.append(sql.SQL("stddev_samp({c})::float8 AS {a}").format(c=ident, a=sql.Identifier(f"sd_{i}")))
            parts.append(sql.SQL("percentile_cont(0.5) WITHIN GROUP (ORDER BY {c})::float8 AS {a}").format(
                c=ident, a=sql.Identifier(f"med_{i}")))
    if target.freshness_column:
        parts.append(sql.SQL("max({c}) FILTER (WHERE {c} <= %(reference_ts)s) AS freshest").format(
            c=sql.Identifier(target.freshness_column)))
    return sql.SQL("SELECT {cols} FROM {rel}").format(cols=sql.SQL(", ").join(parts), rel=target.relation)


def compile_top_values_query(target: ProfileTarget, column: ColumnTarget) -> sql.Composed:
    return sql.SQL(
        "SELECT {c}::text AS value, count(*) AS count FROM {rel} WHERE {c} IS NOT NULL "
        "GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT %(limit)s"
    ).format(c=sql.Identifier(column.name), rel=target.relation)


def compile_sample_query(target: ProfileTarget, column: ColumnTarget) -> sql.Composed:
    """Deterministic pseudo-random distinct examples (ordered by a hash, not by value)."""
    return sql.SQL(
        "SELECT value FROM (SELECT DISTINCT {c}::text AS value FROM {rel} WHERE {c} IS NOT NULL) v "
        "ORDER BY md5(value) LIMIT %(limit)s"
    ).format(c=sql.Identifier(column.name), rel=target.relation)


class Profiler:
    """Profiles catalogued assets and persists the results."""

    def __init__(self, db: Database, settings: Settings, top_n: int = 5, sample_size: int = 5,
                 statement_timeout_ms: int = 60_000) -> None:
        if top_n < 1 or sample_size < 1:
            raise DataTrustError("top_n and sample_size must be positive")
        self.db = db
        self.settings = settings
        self.top_n = top_n
        self.sample_size = sample_size
        self.statement_timeout_ms = statement_timeout_ms

    # ------------------------------------------------------------------ targets
    def targets(self, assets: list[str] | None = None) -> list[ProfileTarget]:
        """Profileable assets (sources and models) from the catalog, optionally filtered by name."""
        self.db.require_metadata("assets", "asset_columns", "profile_runs")
        rows = self.db.fetch_all(
            """
            SELECT a.asset_id, a.name, a.schema_name, a.freshness_column, a.freshness_sla_hours,
                   c.column_id, c.column_name, c.data_type, c.contains_pii
            FROM assets a JOIN asset_columns c ON c.asset_id = a.asset_id AND c.is_active
            WHERE a.is_active AND a.asset_type IN ('source', 'model')
              AND (%(names)s::text[] IS NULL OR a.name = ANY(%(names)s))
            ORDER BY a.name, c.ordinal_position
            """,
            {"names": assets},
        )
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            entry = grouped.setdefault(row["asset_id"], {"row": row, "columns": []})
            entry["columns"].append(ColumnTarget(row["column_id"], row["column_name"], row["data_type"],
                                                 row["contains_pii"]))
        if assets:
            unknown = set(assets) - {e["row"]["name"] for e in grouped.values()}
            if unknown:
                raise DataTrustError(f"Unknown or column-less assets: {', '.join(sorted(unknown))}")
        return [
            ProfileTarget(asset_id=e["row"]["asset_id"], name=e["row"]["name"], schema_name=e["row"]["schema_name"],
                          relation_name=e["row"]["name"], freshness_column=e["row"]["freshness_column"],
                          freshness_sla_hours=e["row"]["freshness_sla_hours"], columns=tuple(e["columns"]))
            for e in grouped.values()
        ]

    # ------------------------------------------------------------------ profiling
    def _existing_columns(self, conn: psycopg.Connection, target: ProfileTarget) -> set[str]:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
            (target.schema_name, target.relation_name),
        ).fetchall()
        return {r["column_name"] for r in rows}

    def profile_asset(self, conn: psycopg.Connection, target: ProfileTarget) -> AssetProfile:
        """Profile one asset on an open connection. Raises DataTrustError if the relation is missing."""
        started = time.perf_counter()
        existing = self._existing_columns(conn, target)
        if not existing:
            raise DataTrustError(f"relation {target.schema_name}.{target.relation_name} does not exist")
        missing = [c.name for c in target.columns if c.name not in existing]
        if missing:
            logger.warning("%s: catalogued columns missing in the warehouse: %s", target.name, ", ".join(missing))
        columns = tuple(c for c in target.columns if c.name in existing)
        fresh_col = target.freshness_column if target.freshness_column in existing else None
        target = ProfileTarget(target.asset_id, target.name, target.schema_name, target.relation_name,
                               fresh_col, target.freshness_sla_hours, columns)

        params = {"reference_ts": self.settings.reference_ts, "limit": self.top_n}
        stats = conn.execute(compile_stats_query(target), params).fetchone()
        row_count = int(stats["row_count"])
        profiles: list[ColumnProfile] = []
        for i, col in enumerate(columns):
            non_null = int(stats[f"nn_{i}"])
            distinct = int(stats[f"d_{i}"])
            profile = ColumnProfile(
                column_id=col.column_id, column_name=col.name,
                null_count=row_count - non_null,
                null_rate=round((row_count - non_null) / row_count, 6) if row_count else 0.0,
                distinct_count=distinct,
                uniqueness=round(distinct / non_null, 6) if non_null else 0.0,
                min_value=stats.get(f"min_{i}"), max_value=stats.get(f"max_{i}"),
                mean_value=stats.get(f"mean_{i}"), stddev_value=stats.get(f"sd_{i}"),
                median_value=stats.get(f"med_{i}"),
            )
            if non_null and not col.contains_pii:
                if distinct < non_null:
                    profile.top_values = [
                        {"value": r["value"], "count": int(r["count"])}
                        for r in conn.execute(compile_top_values_query(target, col), params).fetchall()
                    ]
                profile.sample_values = [
                    r["value"] for r in conn.execute(compile_sample_query(target, col),
                                                     {"limit": self.sample_size}).fetchall()
                ]
            profiles.append(profile)

        freshest, lag, status = freshness(stats.get("freshest"), self.settings.reference_ts,
                                          target.freshness_sla_hours)
        return AssetProfile(
            asset_id=target.asset_id, name=target.name, row_count=row_count, column_count=len(columns),
            freshest_value=freshest, freshness_lag_hours=lag, freshness_status=status,
            duration_ms=int((time.perf_counter() - started) * 1000), columns=profiles,
        )

    def run(self, assets: list[str] | None = None) -> ProfileRunSummary:
        """Profile assets and persist one profile run. Missing relations are skipped and reported."""
        targets = self.targets(assets)
        if not targets:
            raise DataTrustError("No assets to profile; run `datatrust ingest` first.")
        started = time.perf_counter()
        run_id = self.db.fetch_value("INSERT INTO profile_runs (status) VALUES ('running') RETURNING profile_run_id")
        profiles: list[AssetProfile] = []
        skipped: dict[str, str] = {}
        try:
            with self.db.connect(autocommit=True) as conn:
                conn.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(self.statement_timeout_ms)))
                for target in targets:
                    try:
                        profiles.append(self.profile_asset(conn, target))
                    except (DataTrustError, psycopg.Error) as exc:
                        skipped[target.name] = str(exc).strip().splitlines()[0]
                        logger.warning("Skipping %s: %s", target.name, skipped[target.name])
            self._persist(run_id, profiles, skipped)
        except Exception as exc:
            self.db.execute(
                "UPDATE profile_runs SET status = 'failed', finished_at = now(), error_message = %s "
                "WHERE profile_run_id = %s", (str(exc), run_id))
            record_pipeline_event(self.db, "profile", "failed", {"profile_run_id": run_id, "error": str(exc)})
            raise
        summary = ProfileRunSummary(run_id, len(profiles), sum(len(p.columns) for p in profiles), skipped,
                                    int((time.perf_counter() - started) * 1000))
        record_pipeline_event(self.db, "profile", "succeeded", {
            "profile_run_id": run_id, "assets": summary.assets_profiled, "columns": summary.columns_profiled,
            "skipped": skipped})
        logger.info("Profile run %s: %d assets, %d columns, %d skipped in %d ms", run_id, summary.assets_profiled,
                    summary.columns_profiled, len(skipped), summary.duration_ms)
        return summary

    def _persist(self, run_id: int, profiles: list[AssetProfile], skipped: dict[str, str]) -> None:
        if not profiles:
            raise DataTrustError(f"No relation could be profiled ({len(skipped)} skipped); run `datatrust dbt` first.")
        with self.db.transaction() as cur:
            for p in profiles:
                cur.execute(
                    """INSERT INTO asset_profiles (profile_run_id, asset_id, row_count, column_count, freshest_value,
                           freshness_lag_hours, freshness_status, duration_ms)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING asset_profile_id""",
                    (run_id, p.asset_id, p.row_count, p.column_count, p.freshest_value, p.freshness_lag_hours,
                     p.freshness_status, p.duration_ms),
                )
                asset_profile_id = cur.fetchone()["asset_profile_id"]
                cur.executemany(
                    """INSERT INTO column_profiles (asset_profile_id, column_id, null_count, null_rate, distinct_count,
                           uniqueness, min_value, max_value, mean_value, stddev_value, median_value, top_values,
                           sample_values)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [(asset_profile_id, c.column_id, c.null_count, c.null_rate, c.distinct_count, c.uniqueness,
                      c.min_value, c.max_value, c.mean_value, c.stddev_value, c.median_value, to_jsonb(c.top_values),
                      to_jsonb(c.sample_values)) for c in p.columns],
                )
            message = "; ".join(f"{k}: {v}" for k, v in sorted(skipped.items())) or None
            cur.execute(
                "UPDATE profile_runs SET status = 'succeeded', finished_at = now(), asset_count = %s, "
                "error_message = %s WHERE profile_run_id = %s",
                (len(profiles), message, run_id),
            )
