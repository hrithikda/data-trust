"""Quality rule definitions and the YAML loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from datatrust.errors import ConfigurationError

Severity = Literal["low", "medium", "high", "critical"]
Ruleset = Literal["baseline", "improved"]
SEVERITY_ORDER: dict[str, int] = {"low": 1, "medium": 2, "high": 3, "critical": 4}

CATEGORIES: dict[str, str] = {
    "missing_identifier": "Missing identifiers",
    "duplicate_identifier": "Duplicate identifiers",
    "broken_relationship": "Broken relationships",
    "invalid_date": "Invalid or impossible dates",
    "chronology": "Incorrect chronology",
    "invalid_category": "Invalid categorical values",
    "inconsistent_status": "Inconsistent statuses",
    "invalid_numeric": "Invalid numeric values",
    "reconciliation": "Cross-table reconciliation",
    "financial_consistency": "Financial consistency",
    "business_rule": "Business rule violations",
}
IDENTIFIER = r"^[a-z_][a-z0-9_]*$"


class QualityRule(BaseModel):
    """One declarative rule. ``params`` are validated by the rule type's compiler."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    category: str
    type: str
    asset: str = Field(pattern=IDENTIFIER)
    columns: list[str] = Field(default_factory=list)
    severity: Severity
    business_impact: str
    rulesets: list[Ruleset] = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    where: str | None = None
    description: str | None = None
    supersedes: str | None = None
    rationale: str | None = None

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError(f"unknown category '{value}'; expected one of {sorted(CATEGORIES)}")
        return value

    @field_validator("columns")
    @classmethod
    def _identifier_columns(cls, value: list[str]) -> list[str]:
        import re

        bad = [c for c in value if not re.match(IDENTIFIER, c)]
        if bad:
            raise ValueError(f"invalid column identifiers {bad}")
        return value

    @field_validator("where", "rationale", "business_impact")
    @classmethod
    def _normalise(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value else value

    @model_validator(mode="after")
    def _register_type(self) -> QualityRule:
        from datatrust.quality.checks import CHECKS

        check = CHECKS.get(self.type)
        if check is None:
            raise ValueError(f"unknown rule type '{self.type}'; expected one of {sorted(CHECKS)}")
        self.params = check.params_model.model_validate(self.params).model_dump()
        if self.description is None:
            self.description = check.describe(self)
        return self

    def in_ruleset(self, ruleset: str) -> bool:
        return ruleset in self.rulesets

    def referenced_assets(self) -> set[str]:
        """Every asset the rule reads (its own plus joined/aggregated assets)."""
        import re

        assets = {self.asset}
        for key in ("to_asset", "reference_asset", "child_asset"):
            if self.params.get(key):
                assets.add(self.params[key])
        for text in (self.params.get("invalid_when"), self.where):
            assets |= set(re.findall(r"\{ref:([a-z0-9_]+)\}", text or ""))
        return assets


def load_rules(path: Path, ruleset: str | None = None) -> list[QualityRule]:
    """Load rules from YAML, optionally keeping only those in ``ruleset``."""
    if not path.exists():
        raise ConfigurationError(f"Quality rule file not found: {path}")
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    raw_rules = content.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ConfigurationError(f"{path} must define a non-empty 'rules' list")
    rules: list[QualityRule] = []
    errors: list[str] = []
    for index, raw in enumerate(raw_rules):
        try:
            rules.append(QualityRule.model_validate(raw))
        except ValidationError as exc:
            errors.append(f"rule #{index + 1} ({raw.get('key', '?')}): {exc}")
    keys = [r.key for r in rules]
    errors.extend(f"duplicate rule key '{k}'" for k in sorted({k for k in keys if keys.count(k) > 1}))
    known = set(keys)
    errors.extend(f"rule '{r.key}' supersedes unknown rule '{r.supersedes}'"
                  for r in rules if r.supersedes and r.supersedes not in known)
    if errors:
        raise ConfigurationError("Invalid quality rules:\n  - " + "\n  - ".join(errors))
    if ruleset is not None:
        rules = [r for r in rules if r.in_ruleset(ruleset)]
    return rules


def validate_rules_against_metadata(rules: list[QualityRule], columns_by_asset: dict[str, set[str]]) -> None:
    """Fail fast when a rule references an asset or column that dbt metadata does not know."""
    problems: list[str] = []
    for rule in rules:
        for asset in rule.referenced_assets():
            if asset not in columns_by_asset:
                problems.append(f"{rule.key}: unknown asset '{asset}'")
        known = columns_by_asset.get(rule.asset, set())
        problems.extend(f"{rule.key}: unknown column '{rule.asset}.{c}'" for c in rule.columns if known and c not in known)
    if problems:
        raise ConfigurationError("Quality rules do not match warehouse metadata:\n  - " + "\n  - ".join(problems))
