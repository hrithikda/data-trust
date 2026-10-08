"""Business glossary browsing."""

from __future__ import annotations

from typing import Any

from datatrust.db import Database


class GlossaryService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def terms(self, query: str = "", domains: list[str] | None = None,
              statuses: list[str] | None = None) -> list[dict[str, Any]]:
        pattern = f"%{query.strip()}%" if query.strip() else None
        return self.db.fetch_all(
            """
            SELECT g.term_key, g.name, g.definition, g.calculation, g.domain, g.status, g.steward, g.synonyms,
                   g.related_term_keys, t.name AS owner,
                   count(l.link_id) AS link_count,
                   count(DISTINCT l.asset_id) AS asset_count,
                   count(l.column_id) AS column_count
            FROM glossary_terms g
            JOIN teams t ON t.team_id = g.owner_team_id
            LEFT JOIN glossary_term_links l ON l.term_id = g.term_id
            WHERE (%(pattern)s::text IS NULL OR g.name ILIKE %(pattern)s OR g.definition ILIKE %(pattern)s
                   OR array_to_string(g.synonyms, ' ') ILIKE %(pattern)s)
              AND (%(domains)s::text[] IS NULL OR g.domain = ANY(%(domains)s))
              AND (%(statuses)s::text[] IS NULL OR g.status = ANY(%(statuses)s))
            GROUP BY g.term_id, t.name
            ORDER BY g.name
            """,
            {"pattern": pattern, "domains": domains or None, "statuses": statuses or None},
        )

    def links(self, term_key: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT a.name AS asset, a.layer, c.column_name, c.is_critical, c.description AS column_description
               FROM glossary_term_links l JOIN glossary_terms g ON g.term_id = l.term_id
               JOIN assets a ON a.asset_id = l.asset_id LEFT JOIN asset_columns c ON c.column_id = l.column_id
               WHERE g.term_key = %s
               ORDER BY array_position(ARRAY['raw', 'staging', 'intermediate', 'mart', 'exposure'], a.layer),
                        a.name, c.column_name NULLS FIRST""",
            (term_key,))

    def domains(self) -> list[str]:
        return [r["domain"] for r in self.db.fetch_all("SELECT DISTINCT domain FROM glossary_terms ORDER BY 1")]
