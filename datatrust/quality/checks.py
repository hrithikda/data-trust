"""Rule type implementations.

Every check compiles a rule into one SQL query returning the *failing rows* of the rule's
asset (aliased ``p``), optionally with context columns from joined assets. The engine wraps
that query to count failures and fetch samples, so a new rule type only has to describe
which rows are bad.

Identifiers are composed with ``psycopg.sql.Identifier`` and constants with ``sql.Literal``.
Free-form SQL expressions (``invalid_when``, aggregates) come from the version-controlled
rule file, are never user input, and are rejected if they contain statement separators.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from psycopg import sql
from pydantic import BaseModel, ConfigDict, Field, field_validator

from datatrust.errors import RuleExecutionError
from datatrust.quality.resolver import RelationResolver

if TYPE_CHECKING:
    from datatrust.quality.rules import QualityRule

IDENT = r"^[a-z_][a-z0-9_]*$"
ROW_ID = "_dt_row"
"""Synthetic per-row id added to the primary relation so join-based checks count each row once."""
_TOKEN = re.compile(r"\{(reference_ts|ref:[a-z0-9_]+)\}")
_FORBIDDEN = re.compile(r";|--|/\*|\b(insert|update|delete|drop|alter|create|truncate|grant)\b", re.IGNORECASE)


@dataclass
class CompileContext:
    resolver: RelationResolver
    primary: sql.Composable  # "(SELECT ... ) AS p"

    def rel(self, asset: str) -> sql.Composable:
        return self.resolver.relation(asset)

    def expression(self, text: str) -> sql.Composable:
        """Render a trusted SQL expression, expanding {reference_ts} and {ref:asset} tokens."""
        if _FORBIDDEN.search(text):
            raise RuleExecutionError(f"Expression contains a forbidden construct: {text!r}")
        parts: list[sql.Composable] = []
        position = 0
        for match in _TOKEN.finditer(text):
            parts.append(sql.SQL(text[position:match.start()].replace("%", "%%")))
            token = match.group(1)
            parts.append(sql.Placeholder("reference_ts") if token == "reference_ts" else self.rel(token[4:]))
            position = match.end()
        parts.append(sql.SQL(text[position:].replace("%", "%%")))
        return sql.Composed(parts)


def col(alias: str, name: str) -> sql.Composable:
    return sql.Identifier(alias, name)


def _join_condition(join: dict[str, str], left: str, right: str) -> sql.Composable:
    return sql.SQL(" AND ").join(
        sql.SQL("{} = {}").format(col(right, r), col(left, p)) for p, r in join.items()
    )


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _identifier(value: str) -> str:
    if not re.match(IDENT, value):
        raise ValueError(f"'{value}' is not a valid identifier")
    return value


def _join_map(value: dict[str, str]) -> dict[str, str]:
    if not value:
        raise ValueError("join must map at least one column")
    return {_identifier(k): _identifier(v) for k, v in value.items()}


class Check:
    """Base class: subclasses define ``params_model``, ``describe`` and ``failing_rows``."""

    params_model: ClassVar[type[BaseModel]] = _Params
    uses_columns: ClassVar[bool] = False

    def describe(self, rule: QualityRule) -> str:
        raise NotImplementedError

    def failing_rows(self, rule: QualityRule, ctx: CompileContext) -> sql.Composable:
        raise NotImplementedError

    @staticmethod
    def params(rule: QualityRule) -> dict[str, Any]:
        return rule.params


class NotNullCheck(Check):
    uses_columns = True

    def describe(self, rule: QualityRule) -> str:
        return f"{', '.join(rule.columns)} must be populated on every {rule.asset} row."

    def failing_rows(self, rule: QualityRule, ctx: CompileContext) -> sql.Composable:
        if not rule.columns:
            raise RuleExecutionError(f"{rule.key}: not_null needs at least one column")
        predicate = sql.SQL(" OR ").join(sql.SQL("{} IS NULL").format(col("p", c)) for c in rule.columns)
        return sql.SQL("SELECT p.* FROM {} WHERE {}").format(ctx.primary, predicate)


class UniqueCheck(Check):
    uses_columns = True

    def describe(self, rule: QualityRule) -> str:
        return f"Each {', '.join(rule.columns)} value must appear on at most one {rule.asset} row."

    def failing_rows(self, rule: QualityRule, ctx: CompileContext) -> sql.Composable:
        if not rule.columns:
            raise RuleExecutionError(f"{rule.key}: unique needs at least one column")
        keys = sql.SQL(", ").join(col("p", c) for c in rule.columns)
        not_null = sql.SQL(" AND ").join(sql.SQL("{} IS NOT NULL").format(col("p", c)) for c in rule.columns)
        return sql.SQL(
            "SELECT * FROM (SELECT p.*, count(*) OVER (PARTITION BY {keys}) AS duplicate_count "
            "FROM {primary} WHERE {not_null}) d WHERE d.duplicate_count > 1"
        ).format(keys=keys, primary=ctx.primary, not_null=not_null)


class RelationshipParams(_Params):
    column: str
    to_asset: str
    to_column: str
    _v = field_validator("column", "to_asset", "to_column")(classmethod(lambda cls, v: _identifier(v)))


class RelationshipCheck(Check):
    params_model = RelationshipParams

    def describe(self, rule: QualityRule) -> str:
        p = rule.params
        return f"Every non-null {rule.asset}.{p['column']} must exist in {p['to_asset']}.{p['to_column']}."

    def failing_rows(self, rule: QualityRule, ctx: CompileContext) -> sql.Composable:
        p = rule.params
        return sql.SQL(
            "SELECT p.* FROM {primary} WHERE {c} IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM {ref} r WHERE {rc} = {c})"
        ).format(primary=ctx.primary, c=col("p", p["column"]), ref=ctx.rel(p["to_asset"]), rc=col("r", p["to_column"]))


class AcceptedValuesParams(_Params):
    column: str
    values: list[str] = Field(min_length=1)
    _v = field_validator("column")(classmethod(lambda cls, v: _identifier(v)))


class AcceptedValuesCheck(Check):
    params_model = AcceptedValuesParams

    def describe(self, rule: QualityRule) -> str:
        p = rule.params
        return f"{rule.asset}.{p['column']} must be one of: {', '.join(p['values'])}."

    def failing_rows(self, rule: QualityRule, ctx: CompileContext) -> sql.Composable:
        p = rule.params
        values = sql.SQL(", ").join(sql.Literal(v) for v in p["values"])
        return sql.SQL("SELECT p.* FROM {} WHERE {} IS NOT NULL AND {} NOT IN ({})").format(
            ctx.primary, col("p", p["column"]), col("p", p["column"]), values)


class RangeParams(_Params):
    column: str
    min: float | int | str | None = None
    max: float | int | str | None = None
    min_exclusive: bool = False
    max_exclusive: bool = False
    max_reference: bool = False  # upper bound is the data cutoff timestamp
    _v = field_validator("column")(classmethod(lambda cls, v: _identifier(v)))


class RangeCheck(Check):
    params_model = RangeParams

    def describe(self, rule: QualityRule) -> str:
        p = rule.params
        bounds = []
        if p["min"] is not None:
            bounds.append(f"{'>' if p['min_exclusive'] else '>='} {p['min']}")
        if p["max"] is not None:
            bounds.append(f"{'<' if p['max_exclusive'] else '<='} {p['max']}")
        if p["max_reference"]:
            bounds.append("<= the data cutoff")
        scope = f" where {rule.where}" if rule.where else ""
        return f"{rule.asset}.{p['column']} must be {' and '.join(bounds)}{scope}."

    def failing_rows(self, rule: QualityRule, ctx: CompileContext) -> sql.Composable:
        p = rule.params
        c = col("p", p["column"])
        violations: list[sql.Composable] = []
        if p["min"] is not None:
            violations.append(sql.SQL("{} {} {}").format(c, sql.SQL("<=" if p["min_exclusive"] else "<"), sql.Literal(p["min"])))
        if p["max"] is not None:
            violations.append(sql.SQL("{} {} {}").format(c, sql.SQL(">=" if p["max_exclusive"] else ">"), sql.Literal(p["max"])))
        if p["max_reference"]:
            violations.append(sql.SQL("{} > {}").format(c, sql.Placeholder("reference_ts")))
        if not violations:
            raise RuleExecutionError(f"{rule.key}: range needs min, max or max_reference")
        return sql.SQL("SELECT p.* FROM {} WHERE {} IS NOT NULL AND ({})").format(
            ctx.primary, c, sql.SQL(" OR ").join(violations))


class NotFutureParams(_Params):
    column: str
    tolerance_minutes: int = Field(default=0, ge=0)
    _v = field_validator("column")(classmethod(lambda cls, v: _identifier(v)))


class NotFutureCheck(Check):
    params_model = NotFutureParams

    def describe(self, rule: QualityRule) -> str:
        return f"{rule.asset}.{rule.params['column']} must not be later than the data cutoff."

    def failing_rows(self, rule: QualityRule, ctx: CompileContext) -> sql.Composable:
        p = rule.params
        return sql.SQL("SELECT p.* FROM {} WHERE {} > {}::timestamp + make_interval(mins => {})").format(
            ctx.primary, col("p", p["column"]), sql.Placeholder("reference_ts"), sql.Literal(p["tolerance_minutes"]))


class ChronologyParams(_Params):
    later_column: str
    earlier_column: str
    reference_asset: str | None = None
    join: dict[str, str] | None = None
    tolerance_minutes: int = Field(default=0, ge=0)
    _v = field_validator("later_column", "earlier_column")(classmethod(lambda cls, v: _identifier(v)))
    _j = field_validator("join")(classmethod(lambda cls, v: _join_map(v) if v is not None else v))


class ChronologyCheck(Check):
    params_model = ChronologyParams

    def describe(self, rule: QualityRule) -> str:
        p = rule.params
        earlier = f"{p['reference_asset']}.{p['earlier_column']}" if p["reference_asset"] else p["earlier_column"]
        return f"{rule.asset}.{p['later_column']} must not be earlier than {earlier}."

    def failing_rows(self, rule: QualityRule, ctx: CompileContext) -> sql.Composable:
        p = rule.params
        tolerance = sql.SQL("make_interval(mins => {})").format(sql.Literal(p["tolerance_minutes"]))
        later = col("p", p["later_column"])
        if not p["reference_asset"]:
            return sql.SQL("SELECT p.* FROM {} WHERE {} < {} - {}").format(
                ctx.primary, later, col("p", p["earlier_column"]), tolerance)
        if not p["join"]:
            raise RuleExecutionError(f"{rule.key}: chronology with reference_asset needs a join")
        earlier = col("r", p["earlier_column"])
        # DISTINCT ON the synthetic row id: one output row per failing primary row, hash-joinable.
        return sql.SQL(
            "SELECT DISTINCT ON (p.{row_id}) p.*, {earlier} AS {alias} FROM {primary} "
            "JOIN {ref} r ON {join} WHERE {later} < {earlier} - {tol} ORDER BY p.{row_id}"
        ).format(row_id=sql.Identifier(ROW_ID), alias=sql.Identifier(f"reference_{p['earlier_column']}"),
                 primary=ctx.primary, earlier=earlier, ref=ctx.rel(p["reference_asset"]),
                 join=_join_condition(p["join"], "p", "r"), later=later, tol=tolerance)


class RowConditionParams(_Params):
    invalid_when: str

    @field_validator("invalid_when")
    @classmethod
    def _clean(cls, value: str) -> str:
        return " ".join(value.split())


class RowConditionCheck(Check):
    params_model = RowConditionParams

    def describe(self, rule: QualityRule) -> str:
        return f"No {rule.asset} row may satisfy: {rule.params['invalid_when']}."

    def failing_rows(self, rule: QualityRule, ctx: CompileContext) -> sql.Composable:
        return sql.SQL("SELECT p.* FROM {} WHERE ({})").format(ctx.primary, ctx.expression(rule.params["invalid_when"]))


class JoinConditionParams(_Params):
    reference_asset: str
    join: dict[str, str]
    invalid_when: str
    context_columns: list[str] = Field(default_factory=list)
    _j = field_validator("join")(classmethod(lambda cls, v: _join_map(v)))
    _c = field_validator("context_columns")(classmethod(lambda cls, v: [_identifier(c) for c in v]))


class JoinConditionCheck(Check):
    params_model = JoinConditionParams

    def describe(self, rule: QualityRule) -> str:
        p = rule.params
        return f"Joined to {p['reference_asset']}, no {rule.asset} row may satisfy: {' '.join(p['invalid_when'].split())}."

    def failing_rows(self, rule: QualityRule, ctx: CompileContext) -> sql.Composable:
        p = rule.params
        context = p["context_columns"] or list(p["join"].values())
        projection = sql.SQL(", ").join(
            sql.SQL("{} AS {}").format(col("r", c), sql.Identifier(f"reference_{c}")) for c in context)
        return sql.SQL(
            "SELECT DISTINCT ON (p.{row_id}) p.*, {projection} FROM {primary} "
            "JOIN {ref} r ON {join} WHERE ({condition}) ORDER BY p.{row_id}"
        ).format(row_id=sql.Identifier(ROW_ID), primary=ctx.primary, projection=projection,
                 ref=ctx.rel(p["reference_asset"]), join=_join_condition(p["join"], "p", "r"),
                 condition=ctx.expression(p["invalid_when"]))


class AggregateParams(_Params):
    child_asset: str
    join: dict[str, str]
    primary_expression: str
    child_aggregate: str
    child_where: str | None = None
    comparison: Literal["equal", "child_lte_primary"] = "equal"
    tolerance: float = Field(default=0.01, ge=0)
    _j = field_validator("join")(classmethod(lambda cls, v: _join_map(v)))


class AggregateReconciliationCheck(Check):
    params_model = AggregateParams

    def describe(self, rule: QualityRule) -> str:
        p = rule.params
        relation = "equal" if p["comparison"] == "equal" else "not exceed"
        scope = f" ({p['child_where']})" if p["child_where"] else ""
        return (f"For each {rule.asset} row, {p['child_aggregate']} over matching {p['child_asset']} rows{scope} "
                f"must {relation} {p['primary_expression']} (tolerance {p['tolerance']}).")

    def failing_rows(self, rule: QualityRule, ctx: CompileContext) -> sql.Composable:
        p = rule.params
        keys = list(p["join"].items())
        child_keys = sql.SQL(", ").join(
            sql.SQL("{} AS {}").format(col("c", child), sql.Identifier(f"_k{i}")) for i, (_, child) in enumerate(keys))
        group_by = sql.SQL(", ").join(col("c", child) for _, child in keys)
        on = sql.SQL(" AND ").join(
            sql.SQL("{} = {}").format(col("agg", f"_k{i}"), col("p", primary)) for i, (primary, _) in enumerate(keys))
        where = sql.SQL("WHERE {}").format(ctx.expression(p["child_where"])) if p["child_where"] else sql.SQL("")
        primary_value = ctx.expression(p["primary_expression"])
        tolerance = sql.Literal(p["tolerance"])
        if p["comparison"] == "equal":
            violation = sql.SQL("abs(({}) - agg.reconciled_value) > {}").format(primary_value, tolerance)
        else:
            violation = sql.SQL("agg.reconciled_value > ({}) + {}").format(primary_value, tolerance)
        return sql.SQL(
            "SELECT p.*, agg.reconciled_value FROM {primary} JOIN "
            "(SELECT {child_keys}, {aggregate} AS reconciled_value FROM {child} c {where} GROUP BY {group_by}) agg "
            "ON {on} WHERE {violation}"
        ).format(primary=ctx.primary, child_keys=child_keys, aggregate=ctx.expression(p["child_aggregate"]),
                 child=ctx.rel(p["child_asset"]), where=where, group_by=group_by, on=on, violation=violation)


CHECKS: dict[str, Check] = {
    "not_null": NotNullCheck(),
    "unique": UniqueCheck(),
    "relationship": RelationshipCheck(),
    "accepted_values": AcceptedValuesCheck(),
    "range": RangeCheck(),
    "not_future": NotFutureCheck(),
    "chronology": ChronologyCheck(),
    "row_condition": RowConditionCheck(),
    "join_condition": JoinConditionCheck(),
    "aggregate_reconciliation": AggregateReconciliationCheck(),
}
"""Registry of rule types. Register a new ``Check`` subclass here to add a rule type."""
