"""Apply scripted triage actions (status changes and comments) addressed by rule key."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from datatrust.db import Database
from datatrust.errors import ConfigurationError
from datatrust.quality.issues import TRANSITIONS, UNRESOLVED, add_issue_comment, change_issue_status

logger = logging.getLogger(__name__)


class TriageAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str
    status: str
    actor: str = Field(min_length=1)
    note: str | None = None


class TriageComment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str
    actor: str = Field(min_length=1)
    note: str = Field(min_length=1)


class TriagePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[TriageAction] = []
    comments: list[TriageComment] = []


def load_triage(path: Path) -> TriagePlan:
    try:
        plan = TriagePlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ConfigurationError(f"Invalid triage file {path}: {exc}") from exc
    bad = [a.status for a in plan.actions if a.status not in TRANSITIONS]
    if bad:
        raise ConfigurationError(f"Unknown triage statuses: {bad}")
    return plan


def _open_issue_key(db: Database, rule_key: str) -> str | None:
    return db.fetch_value(
        "SELECT i.issue_key FROM issues i JOIN quality_rules r ON r.rule_id = i.rule_id "
        "WHERE r.rule_key = %s AND i.status = ANY(%s)", (rule_key, list(UNRESOLVED)))


def apply_triage(db: Database, path: Path) -> dict[str, int]:
    """Apply every action whose rule currently has an unresolved issue; skip the rest."""
    plan = load_triage(path)
    counts = {"status_changes": 0, "comments": 0, "skipped": 0}
    for action in plan.actions:
        key = _open_issue_key(db, action.rule)
        status = db.fetch_value("SELECT status FROM issues WHERE issue_key = %s", (key,)) if key else None
        if key is None or status == action.status:
            counts["skipped"] += 1
            continue
        change_issue_status(db, key, action.status, action.actor, action.note)
        counts["status_changes"] += 1
    for comment in plan.comments:
        key = _open_issue_key(db, comment.rule)
        if key is None:
            counts["skipped"] += 1
            continue
        add_issue_comment(db, key, comment.actor, comment.note)
        counts["comments"] += 1
    logger.info("Triage applied: %s", counts)
    return counts
