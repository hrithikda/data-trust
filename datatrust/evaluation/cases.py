"""Evaluation suites: labelled cases, each a small set of records across staging tables."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from datatrust.db import json_default
from datatrust.errors import ConfigurationError

Records = dict[str, list[dict[str, Any]]]


class EvaluationCase(BaseModel):
    id: str = Field(pattern=r"^[A-Z]{2}-\d{3}$")
    title: str
    description: str
    expected: Literal["defective", "valid"]
    defect_category: str | None = None
    records: Records = Field(min_length=1)

    @model_validator(mode="after")
    def _category_matches_label(self) -> EvaluationCase:
        if (self.expected == "defective") != (self.defect_category is not None):
            raise ValueError(f"{self.id}: defect_category is required for defective cases and forbidden for valid ones")
        self.description = " ".join(self.description.split())
        return self

    def content_hash(self) -> str:
        payload = json.dumps(self.model_dump(), sort_keys=True, default=json_default)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


class EvaluationSuite(BaseModel):
    suite: Literal["holdout", "regression"]
    description: str
    reference_ts: datetime
    context: Records = Field(default_factory=dict)
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> EvaluationSuite:
        ids = [c.id for c in self.cases]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate case ids: {duplicates}")
        return self

    def cases_hash(self) -> str:
        digest = hashlib.sha256()
        digest.update(json.dumps(self.context, sort_keys=True, default=json_default).encode())
        for case in sorted(self.cases, key=lambda c: c.id):
            digest.update(case.content_hash().encode())
        return digest.hexdigest()[:16]

    def label_counts(self) -> dict[str, int]:
        return {label: sum(c.expected == label for c in self.cases) for label in ("defective", "valid")}

    def tables(self) -> set[str]:
        names = set(self.context)
        for case in self.cases:
            names |= set(case.records)
        return names


def load_suite(path: Path) -> EvaluationSuite:
    if not path.exists():
        raise ConfigurationError(f"Evaluation suite not found: {path}")
    try:
        return EvaluationSuite.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigurationError(f"Invalid evaluation suite {path}: {exc}") from exc
