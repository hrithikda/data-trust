"""Governance configuration (teams, source systems, glossary) loaded from YAML and validated."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from datatrust.errors import ConfigurationError

KEY_PATTERN = r"^[a-z][a-z0-9_]*$"


class Team(BaseModel):
    key: str = Field(pattern=KEY_PATTERN)
    name: str
    description: str
    lead: str
    email: str
    slack: str
    on_call: str | None = None


class SourceSystem(BaseModel):
    key: str = Field(pattern=KEY_PATTERN)
    name: str
    system_type: str
    vendor: str
    description: str
    owner: str
    ingestion_method: str
    load_frequency: str


class GlossaryTerm(BaseModel):
    key: str = Field(pattern=KEY_PATTERN)
    name: str
    domain: str
    owner: str
    steward: str
    status: Literal["draft", "approved", "deprecated"]
    definition: str
    calculation: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    links: list[str] = Field(min_length=1)

    @field_validator("definition")
    @classmethod
    def _strip(cls, value: str) -> str:
        return " ".join(value.split())

    def parsed_links(self) -> list[tuple[str, str | None]]:
        """Split ``asset.column`` links into (asset, column) pairs."""
        pairs: list[tuple[str, str | None]] = []
        for link in self.links:
            asset, _, column = link.partition(".")
            pairs.append((asset, column or None))
        return pairs


class Governance(BaseModel):
    teams: list[Team]
    source_systems: list[SourceSystem]
    terms: list[GlossaryTerm]

    @model_validator(mode="after")
    def _references_resolve(self) -> Governance:
        team_keys = {t.key for t in self.teams}
        term_keys = {t.key for t in self.terms}
        problems: list[str] = []
        for duplicate in _duplicates([t.key for t in self.teams]) | _duplicates([t.key for t in self.terms]):
            problems.append(f"duplicate key '{duplicate}'")
        for system in self.source_systems:
            if system.owner not in team_keys:
                problems.append(f"source system '{system.key}' has unknown owner '{system.owner}'")
        for term in self.terms:
            if term.owner not in team_keys:
                problems.append(f"term '{term.key}' has unknown owner '{term.owner}'")
            problems.extend(f"term '{term.key}' relates to unknown term '{r}'" for r in term.related if r not in term_keys)
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def team_keys(self) -> set[str]:
        return {t.key for t in self.teams}

    @property
    def source_system_keys(self) -> set[str]:
        return {s.key for s in self.source_systems}


def _duplicates(values: list[str]) -> set[str]:
    seen: set[str] = set()
    return {v for v in values if v in seen or seen.add(v)}  # type: ignore[func-returns-value]


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigurationError(f"Governance file not found: {path}")
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(content, dict):
        raise ConfigurationError(f"{path} must contain a mapping at the top level")
    return content


def load_governance(config_dir: Path) -> Governance:
    """Load and cross-validate ``config/governance/*.yml``."""
    directory = config_dir / "governance"
    try:
        return Governance(
            teams=_read_yaml(directory / "teams.yml").get("teams", []),
            source_systems=_read_yaml(directory / "source_systems.yml").get("source_systems", []),
            terms=_read_yaml(directory / "glossary.yml").get("terms", []),
        )
    except ValidationError as exc:
        raise ConfigurationError(f"Invalid governance configuration: {exc}") from exc
