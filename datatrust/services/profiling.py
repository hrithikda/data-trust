"""Read-side access to persisted profiles."""

from __future__ import annotations

from typing import Any

from datatrust.db import Database
from datatrust.errors import DataTrustError


class ProfilingService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def runs(self) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT profile_run_id, started_at, finished_at, status, asset_count, error_message
               FROM profile_runs ORDER BY profile_run_id DESC""")

    def freshness_overview(self) -> list[dict[str, Any]]:
        """Latest profile of every asset: size and freshness against its SLA."""
        return self.db.fetch_all(
            """
            SELECT DISTINCT ON (a.name) a.name AS asset, a.layer, t.name AS owner, a.freshness_column,
                   a.freshness_sla_hours, ap.row_count, ap.column_count, ap.freshest_value,
                   ap.freshness_lag_hours::float AS freshness_lag_hours, ap.freshness_status, ap.duration_ms,
                   ap.profile_run_id
            FROM asset_profiles ap
            JOIN assets a ON a.asset_id = ap.asset_id
            LEFT JOIN teams t ON t.team_id = a.owner_team_id
            ORDER BY a.name, ap.profile_run_id DESC
            """)

    def column_profiles(self, asset: str, profile_run_id: int | None = None) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """
            WITH target AS (
                SELECT ap.asset_profile_id FROM asset_profiles ap JOIN assets a ON a.asset_id = ap.asset_id
                WHERE a.name = %(asset)s AND (%(run)s::int IS NULL OR ap.profile_run_id = %(run)s)
                ORDER BY ap.profile_run_id DESC LIMIT 1
            )
            SELECT c.column_name, c.data_type, c.is_critical, c.cde_category, c.contains_pii,
                   cp.null_count, cp.null_rate::float AS null_rate, cp.distinct_count,
                   cp.uniqueness::float AS uniqueness, cp.min_value, cp.max_value, cp.mean_value, cp.stddev_value,
                   cp.median_value, cp.top_values, cp.sample_values
            FROM column_profiles cp
            JOIN target ON target.asset_profile_id = cp.asset_profile_id
            JOIN asset_columns c ON c.column_id = cp.column_id
            ORDER BY c.ordinal_position
            """,
            {"asset": asset, "run": profile_run_id},
        )
        if not rows:
            raise DataTrustError(f"No profile stored for '{asset}'. Run `datatrust profile`.")
        return rows

    def history(self, asset: str) -> list[dict[str, Any]]:
        """Row count and freshness of an asset across profile runs (for comparison over time)."""
        return self.db.fetch_all(
            """SELECT ap.profile_run_id, pr.started_at, ap.row_count, ap.freshness_lag_hours::float AS lag_hours,
                      ap.freshness_status
               FROM asset_profiles ap JOIN assets a ON a.asset_id = ap.asset_id
               JOIN profile_runs pr ON pr.profile_run_id = ap.profile_run_id
               WHERE a.name = %s ORDER BY ap.profile_run_id""",
            (asset,))
