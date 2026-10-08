"""Map logical asset names used by rules to physical relations.

The same rule set runs against the warehouse (relations from dbt metadata) or against the
evaluation sandbox (one schema holding a small table per staging model).
"""

from __future__ import annotations

from typing import Protocol

from psycopg import sql

from datatrust.errors import RuleExecutionError


class RelationResolver(Protocol):
    def relation(self, asset: str) -> sql.Composable: ...

    def loaded_at_column(self, asset: str) -> str | None: ...


class WarehouseResolver:
    """Resolve assets via the ``assets`` metadata table."""

    def __init__(self, db) -> None:
        rows = db.fetch_all(
            """
            SELECT a.name, a.schema_name, a.asset_type,
                   CASE WHEN a.layer = 'raw' THEN NULL
                        WHEN EXISTS (SELECT 1 FROM asset_columns c WHERE c.asset_id = a.asset_id
                                     AND c.column_name = 'loaded_at' AND c.is_active) THEN 'loaded_at' END AS loaded_at
            FROM assets a WHERE a.is_active AND a.asset_type IN ('source', 'model')
            """
        )
        self._relations = {r["name"]: (r["schema_name"], r["name"]) for r in rows}
        self._loaded_at = {r["name"]: r["loaded_at"] for r in rows}

    def relation(self, asset: str) -> sql.Composable:
        if asset not in self._relations:
            raise RuleExecutionError(f"Asset '{asset}' is not in the metadata catalog; run `datatrust ingest`.")
        schema, name = self._relations[asset]
        return sql.Identifier(schema, name)

    def loaded_at_column(self, asset: str) -> str | None:
        return self._loaded_at.get(asset)

    def known_assets(self) -> set[str]:
        return set(self._relations)


class SchemaResolver:
    """Resolve every asset to ``<schema>.<asset>`` (used by the evaluation sandbox and tests)."""

    def __init__(self, schema: str, loaded_at: str | None = None) -> None:
        self.schema = schema
        self._loaded_at = loaded_at

    def relation(self, asset: str) -> sql.Composable:
        return sql.Identifier(self.schema, asset)

    def loaded_at_column(self, asset: str) -> str | None:
        return self._loaded_at
