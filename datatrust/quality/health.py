"""Quality health scoring.

Each executed rule scores 1.0 when it passes. A failing rule scores at most 0.5 (any failure
costs at least half its weight) and falls linearly to 0 as the failure rate reaches 5%.
Health is the severity-weighted mean of rule scores, scaled to 0-100; errored rules are
excluded because they say nothing about the data.
"""

from __future__ import annotations

from collections.abc import Iterable

from datatrust.quality.rules import SEVERITY_ORDER

FULL_PENALTY_RATE = 0.05


def rule_score(status: str, failure_rate: float) -> float | None:
    if status == "error":
        return None
    if status == "pass":
        return 1.0
    return round(0.5 * (1 - min(1.0, failure_rate / FULL_PENALTY_RATE)), 4)


def severity_weight(severity: str) -> int:
    return SEVERITY_ORDER[severity]


def health_score(scored: Iterable[tuple[str, float | None]]) -> float | None:
    """Weighted health from (severity, rule_score) pairs; None when nothing was scored."""
    pairs = [(severity_weight(s), score) for s, score in scored if score is not None]
    total = sum(w for w, _ in pairs)
    return round(100 * sum(w * score for w, score in pairs) / total, 2) if total else None
