"""Write governance config and parsed dbt metadata into the metadata schema.

Ingestion is idempotent and runs in one transaction: assets and columns are upserted by
their natural keys (so ids referenced by rules, issues and profiles stay stable), objects
that disappeared from dbt are flagged inactive, and lineage edges are rebuilt from the
manifest on every run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import psycopg

from datatrust.errors import ConfigurationError
from datatrust.metadata.dbt_artifacts import DbtProject, parse_artifacts
from datatrust.metadata.governance import Governance, load_governance

logger = logging.getLogger(__name__)


@dataclass
class IngestionSummary:
    teams: int
    source_systems: int
    assets: int
    columns: int
    critical_columns: int
    dependencies: int
    glossary_terms: int
    glossary_links: int
    dbt_tests: int
    dbt_version: str

    def as_dict(self) -> dict[str, int | str]:
        return dict(self.__dict__)


def validate_cross_references(governance: Governance, project: DbtProject) -> None:
    """Every owner, source system and glossary link must resolve before anything is written."""
    problems: list[str] = []
    assets = {a.name: a for a in project.assets}
    for asset in project.assets:
        if asset.owner is None:
            problems.append(f"{asset.name}: missing meta.owner")
        elif asset.owner not in governance.team_keys:
            problems.append(f"{asset.name}: unknown owner team '{asset.owner}'")
        if asset.asset_type == "source" and asset.source_system not in governance.source_system_keys:
            problems.append(f"{asset.name}: unknown source system '{asset.source_system}'")
    for term in governance.terms:
        for asset_name, column in term.parsed_links():
            asset = assets.get(asset_name)
            if asset is None:
                problems.append(f"glossary '{term.key}': unknown asset '{asset_name}'")
            elif column and column not in {c.name for c in asset.columns}:
                problems.append(f"glossary '{term.key}': unknown column '{asset_name}.{column}'")
    if problems:
        raise ConfigurationError("Metadata cross-reference errors:\n  - " + "\n  - ".join(problems))


def ingest_metadata(db, config_dir: Path, target_dir: Path) -> IngestionSummary:
    """Load governance + dbt artifacts and persist them. Returns row counts."""
    db.require_metadata("assets", "teams")
    governance = load_governance(config_dir)
    project = parse_artifacts(target_dir)
    validate_cross_references(governance, project)

    with db.transaction() as cur:
        team_ids = _upsert_teams(cur, governance)
        system_ids = _upsert_source_systems(cur, governance, team_ids)
        asset_ids = _upsert_assets(cur, project, team_ids)
        column_ids = _upsert_columns(cur, project, asset_ids)
        dependency_count = _replace_dependencies(cur, project, asset_ids)
        _upsert_source_mappings(cur, project, asset_ids, system_ids)
        term_count, link_count = _replace_glossary(cur, governance, team_ids, asset_ids, column_ids)
        test_count = _upsert_tests(cur, project, asset_ids)

    summary = IngestionSummary(
        teams=len(team_ids), source_systems=len(system_ids), assets=len(asset_ids),
        columns=len(column_ids), critical_columns=sum(c.is_critical for a in project.assets for c in a.columns),
        dependencies=dependency_count, glossary_terms=term_count, glossary_links=link_count,
        dbt_tests=test_count, dbt_version=project.dbt_version,
    )
    logger.info("Metadata ingested: %s", summary.as_dict())
    return summary


def _upsert_teams(cur: psycopg.Cursor, governance: Governance) -> dict[str, int]:
    ids: dict[str, int] = {}
    for team in governance.teams:
        cur.execute(
            """
            INSERT INTO teams (team_key, name, description, lead_name, email, slack_channel, on_call_rotation)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (team_key) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
                lead_name = EXCLUDED.lead_name, email = EXCLUDED.email, slack_channel = EXCLUDED.slack_channel,
                on_call_rotation = EXCLUDED.on_call_rotation
            RETURNING team_id
            """,
            (team.key, team.name, team.description, team.lead, team.email, team.slack, team.on_call),
        )
        ids[team.key] = cur.fetchone()["team_id"]
    return ids


def _upsert_source_systems(cur: psycopg.Cursor, governance: Governance, team_ids: dict[str, int]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for system in governance.source_systems:
        cur.execute(
            """
            INSERT INTO source_systems (system_key, name, system_type, vendor, description, owner_team_id,
                                        ingestion_method, load_frequency)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (system_key) DO UPDATE SET name = EXCLUDED.name, system_type = EXCLUDED.system_type,
                vendor = EXCLUDED.vendor, description = EXCLUDED.description, owner_team_id = EXCLUDED.owner_team_id,
                ingestion_method = EXCLUDED.ingestion_method, load_frequency = EXCLUDED.load_frequency
            RETURNING source_system_id
            """,
            (system.key, system.name, system.system_type, system.vendor, system.description,
             team_ids[system.owner], system.ingestion_method, system.load_frequency),
        )
        ids[system.key] = cur.fetchone()["source_system_id"]
    return ids


def _upsert_assets(cur: psycopg.Cursor, project: DbtProject, team_ids: dict[str, int]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for a in project.assets:
        cur.execute(
            """
            INSERT INTO assets (unique_id, name, asset_type, layer, database_name, schema_name, relation_name,
                description, domain, owner_team_id, criticality, materialization, exposure_type, audience,
                is_financial_reporting, is_customer_facing, contains_pii, freshness_column, freshness_sla_hours,
                primary_key, tags, file_path, url, is_active, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, now())
            ON CONFLICT (unique_id) DO UPDATE SET name = EXCLUDED.name, asset_type = EXCLUDED.asset_type,
                layer = EXCLUDED.layer, database_name = EXCLUDED.database_name, schema_name = EXCLUDED.schema_name,
                relation_name = EXCLUDED.relation_name, description = EXCLUDED.description, domain = EXCLUDED.domain,
                owner_team_id = EXCLUDED.owner_team_id, criticality = EXCLUDED.criticality,
                materialization = EXCLUDED.materialization, exposure_type = EXCLUDED.exposure_type,
                audience = EXCLUDED.audience, is_financial_reporting = EXCLUDED.is_financial_reporting,
                is_customer_facing = EXCLUDED.is_customer_facing, contains_pii = EXCLUDED.contains_pii,
                freshness_column = EXCLUDED.freshness_column, freshness_sla_hours = EXCLUDED.freshness_sla_hours,
                primary_key = EXCLUDED.primary_key, tags = EXCLUDED.tags, file_path = EXCLUDED.file_path,
                url = EXCLUDED.url, is_active = true, updated_at = now()
            RETURNING asset_id
            """,
            (a.unique_id, a.name, a.asset_type, a.layer, a.database, a.schema, a.relation_name, a.description,
             a.domain, team_ids.get(a.owner or ""), a.criticality, a.materialization, a.exposure_type, a.audience,
             a.is_financial_reporting, a.is_customer_facing, a.contains_pii, a.freshness_column,
             a.freshness_sla_hours, a.primary_key, a.tags, a.file_path, a.url),
        )
        ids[a.name] = cur.fetchone()["asset_id"]
    cur.execute("UPDATE assets SET is_active = false, updated_at = now() WHERE NOT (asset_id = ANY(%s))",
                (list(ids.values()),))
    return ids


def _upsert_columns(cur: psycopg.Cursor, project: DbtProject, asset_ids: dict[str, int]) -> dict[tuple[str, str], int]:
    ids: dict[tuple[str, str], int] = {}
    for asset in project.assets:
        asset_id = asset_ids[asset.name]
        for c in asset.columns:
            cur.execute(
                """
                INSERT INTO asset_columns (asset_id, column_name, ordinal_position, data_type, description,
                    is_critical, cde_category, critical_reason, contains_pii, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true)
                ON CONFLICT (asset_id, column_name) DO UPDATE SET ordinal_position = EXCLUDED.ordinal_position,
                    data_type = EXCLUDED.data_type, description = EXCLUDED.description,
                    is_critical = EXCLUDED.is_critical, cde_category = EXCLUDED.cde_category,
                    critical_reason = EXCLUDED.critical_reason, contains_pii = EXCLUDED.contains_pii, is_active = true
                RETURNING column_id
                """,
                (asset_id, c.name, c.ordinal, c.data_type, c.description, c.is_critical, c.cde_category,
                 c.critical_reason, c.contains_pii),
            )
            ids[(asset.name, c.name)] = cur.fetchone()["column_id"]
    cur.execute("UPDATE asset_columns SET is_active = false WHERE NOT (column_id = ANY(%s))", (list(ids.values()),))
    return ids


def _replace_dependencies(cur: psycopg.Cursor, project: DbtProject, asset_ids: dict[str, int]) -> int:
    by_uid = {a.unique_id: asset_ids[a.name] for a in project.assets}
    edges = sorted(
        (by_uid[d.upstream], by_uid[d.downstream], d.dependency_type, d.referenced_columns)
        for d in project.dependencies if d.upstream in by_uid and d.downstream in by_uid
    )
    cur.execute("DELETE FROM asset_dependencies")
    cur.executemany(
        """INSERT INTO asset_dependencies (upstream_asset_id, downstream_asset_id, dependency_type, referenced_columns)
           VALUES (%s, %s, %s, %s)""",
        edges,
    )
    return len(edges)


def _upsert_source_mappings(cur: psycopg.Cursor, project: DbtProject, asset_ids: dict[str, int],
                            system_ids: dict[str, int]) -> None:
    for asset in project.assets:
        if asset.asset_type != "source" or not asset.source_system:
            continue
        cur.execute(
            """
            INSERT INTO source_mappings (asset_id, source_system_id, source_object, extraction_notes)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (asset_id) DO UPDATE SET source_system_id = EXCLUDED.source_system_id,
                source_object = EXCLUDED.source_object, extraction_notes = EXCLUDED.extraction_notes
            """,
            (asset_ids[asset.name], system_ids[asset.source_system], asset.source_object or asset.name,
             f"Loaded into {asset.relation_name}; load timestamp column {asset.loaded_at_column or 'n/a'}"),
        )


def _replace_glossary(cur: psycopg.Cursor, governance: Governance, team_ids: dict[str, int],
                      asset_ids: dict[str, int], column_ids: dict[tuple[str, str], int]) -> tuple[int, int]:
    links = 0
    keys = [t.key for t in governance.terms]
    cur.execute("DELETE FROM glossary_terms WHERE NOT (term_key = ANY(%s))", (keys,))
    for term in governance.terms:
        cur.execute(
            """
            INSERT INTO glossary_terms (term_key, name, definition, calculation, domain, owner_team_id, steward,
                                        status, synonyms, related_term_keys)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (term_key) DO UPDATE SET name = EXCLUDED.name, definition = EXCLUDED.definition,
                calculation = EXCLUDED.calculation, domain = EXCLUDED.domain, owner_team_id = EXCLUDED.owner_team_id,
                steward = EXCLUDED.steward, status = EXCLUDED.status, synonyms = EXCLUDED.synonyms,
                related_term_keys = EXCLUDED.related_term_keys
            RETURNING term_id
            """,
            (term.key, term.name, term.definition, term.calculation, term.domain, team_ids[term.owner],
             term.steward, term.status, term.synonyms, term.related),
        )
        term_id = cur.fetchone()["term_id"]
        cur.execute("DELETE FROM glossary_term_links WHERE term_id = %s", (term_id,))
        for asset_name, column in term.parsed_links():
            cur.execute(
                "INSERT INTO glossary_term_links (term_id, asset_id, column_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (term_id, asset_ids[asset_name], column_ids.get((asset_name, column)) if column else None),
            )
            links += 1
    return len(governance.terms), links


def _upsert_tests(cur: psycopg.Cursor, project: DbtProject, asset_ids: dict[str, int]) -> int:
    by_uid = {a.unique_id: asset_ids[a.name] for a in project.assets}
    uids = [t.unique_id for t in project.tests]
    cur.execute("DELETE FROM dbt_tests WHERE NOT (unique_id = ANY(%s))", (uids,))
    for t in project.tests:
        cur.execute(
            """
            INSERT INTO dbt_tests (unique_id, asset_id, column_name, test_name, severity, last_status,
                                   last_failures, last_run_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (unique_id) DO UPDATE SET asset_id = EXCLUDED.asset_id, column_name = EXCLUDED.column_name,
                test_name = EXCLUDED.test_name, severity = EXCLUDED.severity, last_status = EXCLUDED.last_status,
                last_failures = EXCLUDED.last_failures, last_run_at = EXCLUDED.last_run_at
            """,
            (t.unique_id, by_uid.get(t.attached_to or ""), t.column_name, t.test_name, t.severity, t.status,
             t.failures, t.executed_at),
        )
    return len(project.tests)
