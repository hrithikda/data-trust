"""Execute quality rules against real records.

The engine is persistence-free: it compiles each rule, runs it on one autocommit connection
(so a failing rule cannot poison the others) and returns structured outcomes. Callers decide
whether to store them (warehouse runs) or only score them (evaluation).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql

from datatrust.db import Database
from datatrust.errors import RuleExecutionError
from datatrust.quality.checks import CHECKS, ROW_ID, CompileContext
from datatrust.quality.resolver import RelationResolver
from datatrust.quality.rules import QualityRule

logger = logging.getLogger(__name__)


@dataclass
class RuleOutcome:
    rule: QualityRule
    status: str  # pass | fail | error
    records_scanned: int = 0
    records_failed: int = 0
    samples: list[dict[str, Any]] = field(default_factory=list)
    execution_ms: int = 0
    error: str | None = None

    @property
    def failure_rate(self) -> float:
        return self.records_failed / self.records_scanned if self.records_scanned else 0.0

    @property
    def failed(self) -> bool:
        return self.status == "fail"


class QualityEngine:
    """Compile and run rules through a :class:`RelationResolver`."""

    def __init__(self, db: Database, resolver: RelationResolver, sample_size: int = 5,
                 statement_timeout_ms: int = 60_000) -> None:
        if sample_size < 0:
            raise ValueError("sample_size must be >= 0")
        self.db = db
        self.resolver = resolver
        self.sample_size = sample_size
        self.statement_timeout_ms = statement_timeout_ms

    def primary_relation(self, rule: QualityRule, filter_by_load_time: bool) -> sql.Composable:
        """The rule's asset, restricted to its scope and (for backfills) rows loaded by the cutoff."""
        ctx = CompileContext(self.resolver, sql.SQL(""))
        filters: list[sql.Composable] = []
        if rule.where:
            filters.append(sql.SQL("({})").format(ctx.expression(rule.where)))
        loaded_at = self.resolver.loaded_at_column(rule.asset) if filter_by_load_time else None
        if loaded_at:
            filters.append(sql.SQL("{} <= {}").format(sql.Identifier(loaded_at), sql.Placeholder("reference_ts")))
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(filters) if filters else sql.SQL("")
        return sql.SQL("(SELECT t.*, row_number() OVER () AS {} FROM {} t{}) AS p").format(
            sql.Identifier(ROW_ID), self.resolver.relation(rule.asset), where)

    def compile(self, rule: QualityRule, filter_by_load_time: bool = False) -> tuple[sql.Composable, sql.Composable]:
        """Return (scanned-count query, failing-rows query) for a rule."""
        primary = self.primary_relation(rule, filter_by_load_time)
        failing = CHECKS[rule.type].failing_rows(rule, CompileContext(self.resolver, primary))
        return sql.SQL("SELECT count(*) AS n FROM {}").format(primary), failing

    def execute(self, rules: list[QualityRule], as_of: datetime, filter_by_load_time: bool = False) -> list[RuleOutcome]:
        params = {"reference_ts": as_of}
        outcomes: list[RuleOutcome] = []
        with self.db.connect(autocommit=True) as conn:
            conn.execute(sql.SQL("SET statement_timeout = {}").format(sql.Literal(self.statement_timeout_ms)))
            for rule in rules:
                outcomes.append(self._execute_one(conn, rule, params, filter_by_load_time))
        failed = sum(o.failed for o in outcomes)
        errored = sum(o.status == "error" for o in outcomes)
        logger.debug("Executed %d rules as of %s: %d failed, %d errored", len(outcomes), as_of, failed, errored)
        return outcomes

    def _execute_one(self, conn: psycopg.Connection, rule: QualityRule, params: dict[str, Any],
                     filter_by_load_time: bool) -> RuleOutcome:
        started = time.perf_counter()
        try:
            scanned_query, failing_query = self.compile(rule, filter_by_load_time)
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT ({scanned}) AS scanned, (SELECT count(*) FROM ({failing}) f) AS failed")
                            .format(scanned=scanned_query, failing=failing_query), params)
                counts = cur.fetchone()
                samples: list[dict[str, Any]] = []
                if counts["failed"] and self.sample_size:
                    cur.execute(sql.SQL("SELECT * FROM ({}) f ORDER BY 1 LIMIT {}")
                                .format(failing_query, sql.Literal(self.sample_size)), params)
                    samples = [{k: v for k, v in row.items() if k != ROW_ID} for row in cur.fetchall()]
            return RuleOutcome(
                rule=rule, status="fail" if counts["failed"] else "pass",
                records_scanned=int(counts["scanned"]), records_failed=int(counts["failed"]),
                samples=samples, execution_ms=int((time.perf_counter() - started) * 1000),
            )
        except (psycopg.Error, RuleExecutionError) as exc:
            logger.warning("Rule %s errored: %s", rule.key, exc)
            return RuleOutcome(rule=rule, status="error", error=str(exc).strip()[:2000],
                               execution_ms=int((time.perf_counter() - started) * 1000))
