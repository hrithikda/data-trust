"""Transparent, additive priority scoring.

score = sum of capped factor points (0-100). The defect group measures the broken records
(severity, volume, rate, dataset and field criticality); the blast-radius group measures what
depends on them (downstream assets, critical consumers, financial / executive / customer-facing
outputs, number of teams). Volume is deliberately only 15 of 100 points: a handful of broken
order identifiers that reach the board deck outranks thousands of bad values in a field nobody
downstream reads.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from datatrust.errors import ConfigurationError
from datatrust.impact.analysis import ImpactReport

EXPECTED_FACTORS = {
    "severity", "affected_volume", "affected_rate", "dataset_criticality", "field_criticality",
    "downstream_dependencies", "critical_downstream", "financial_reporting", "executive_or_customer_facing",
    "multi_team",
}


@dataclass
class IssueSignals:
    """Facts about the failing records."""

    severity: str
    records_failed: int
    records_scanned: int
    asset_criticality: str
    critical_columns: list[tuple[str, str]] = field(default_factory=list)  # (column, cde_category)

    @property
    def failure_rate(self) -> float:
        return self.records_failed / self.records_scanned if self.records_scanned else 0.0


@dataclass
class PriorityFactor:
    key: str
    label: str
    group: str
    points: float
    max_points: float
    reason: str


@dataclass
class PriorityResult:
    score: float
    band: str
    factors: list[PriorityFactor]

    @property
    def defect_points(self) -> float:
        return round(sum(f.points for f in self.factors if f.group == "defect"), 1)

    @property
    def blast_radius_points(self) -> float:
        return round(sum(f.points for f in self.factors if f.group == "blast_radius"), 1)

    def breakdown(self) -> list[dict[str, Any]]:
        return [asdict(f) for f in self.factors]

    def top_reasons(self, n: int = 3) -> list[str]:
        ranked = sorted(self.factors, key=lambda f: f.points, reverse=True)
        return [f.reason for f in ranked[:n] if f.points > 0]


class PriorityModel:
    """Loads weights from ``config/priority_model.yml`` and scores issues."""

    def __init__(self, config: dict[str, Any]) -> None:
        factors = config.get("factors") or {}
        missing = EXPECTED_FACTORS - set(factors)
        if missing:
            raise ConfigurationError(f"priority model is missing factors: {sorted(missing)}")
        total = sum(float(f["max_points"]) for f in factors.values())
        if abs(total - 100) > 1e-6:
            raise ConfigurationError(f"priority factor maxima must sum to 100, got {total}")
        self.factors = factors
        self.bands = sorted(((int(v), k) for k, v in (config.get("bands") or {}).items()), reverse=True)
        self.impact_levels = sorted(((int(v), k) for k, v in (config.get("impact_levels") or {}).items()), reverse=True)
        if not self.bands or self.bands[-1][0] != 0:
            raise ConfigurationError("priority bands must include a band starting at 0")

    @classmethod
    def from_file(cls, path: Path) -> PriorityModel:
        if not path.exists():
            raise ConfigurationError(f"Priority model not found: {path}")
        try:
            return cls(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        except (yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError(f"Invalid priority model {path}: {exc}") from exc

    # ------------------------------------------------------------------ factors
    def _factor(self, key: str, label: str, points: float, reason: str) -> PriorityFactor:
        cfg = self.factors[key]
        capped = round(max(0.0, min(float(cfg["max_points"]), points)), 1)
        return PriorityFactor(key, label, cfg["group"], capped, float(cfg["max_points"]), reason)

    def defect_factors(self, s: IssueSignals) -> list[PriorityFactor]:
        f = self.factors
        severity = f["severity"]["points_by_severity"].get(s.severity, 0)
        volume_cfg = f["affected_volume"]
        volume = volume_cfg["max_points"] * min(1.0, math.log10(s.records_failed + 1)
                                                 / math.log10(volume_cfg["saturation_records"] + 1))
        rate = f["affected_rate"]["max_points"] * min(1.0, s.failure_rate / f["affected_rate"]["saturation_rate"])
        dataset = f["dataset_criticality"]["points_by_criticality"].get(s.asset_criticality, 0)
        field_cfg = f["field_criticality"]
        categories = {category for _, category in s.critical_columns}
        sensitive = sorted(categories & set(field_cfg["sensitive_categories"]))
        field_points = (field_cfg["critical_field_points"] if s.critical_columns else 0) + (
            field_cfg["sensitive_category_points"] if sensitive else 0)
        if s.critical_columns:
            names = ", ".join(c for c, _ in s.critical_columns)
            field_reason = f"Critical data element(s) affected: {names}" + (
                f" ({', '.join(sensitive)})" if sensitive else "")
        else:
            field_reason = "No critical data elements involved"
        return [
            self._factor("severity", "Rule severity", severity, f"Rule severity is {s.severity}"),
            self._factor("affected_volume", "Affected records", volume, f"{s.records_failed:,} records fail the rule"),
            self._factor("affected_rate", "Affected share", rate, f"{s.failure_rate:.2%} of {s.records_scanned:,} scanned records fail"),
            self._factor("dataset_criticality", "Dataset criticality", dataset, f"Dataset criticality is {s.asset_criticality}"),
            self._factor("field_criticality", "Field criticality", field_points, field_reason),
        ]

    def blast_radius_factors(self, impact: ImpactReport) -> list[PriorityFactor]:
        f = self.factors
        n = impact.downstream_count
        downstream = f["downstream_dependencies"]["max_points"] * min(1.0, n / f["downstream_dependencies"]["saturation_assets"])
        pruned = f" ({len(impact.unaffected_consumers)} consumer(s) do not read the affected columns)" \
            if impact.unaffected_consumers else ""
        critical = impact.critical_assets
        critical_points = f["critical_downstream"]["points_per_asset"] * len(critical)
        exec_cfg = f["executive_or_customer_facing"]
        if impact.executive_affected:
            hops = impact.hops_to_executive or 0
            extra_hops = max(0, hops - exec_cfg["executive_free_hops"])
            exposure_points = max(exec_cfg["executive_min_points"],
                                  exec_cfg["executive_points"] - exec_cfg["executive_hop_decay"] * extra_hops)
            exposure_reason = f"Executive reporting is exposed, {hops} transformation(s) downstream"
        elif impact.customer_facing_affected:
            exposure_points = exec_cfg["customer_facing_points"]
            exposure_reason = "A customer-facing output is exposed"
        else:
            exposure_points, exposure_reason = 0, "No executive or customer-facing output depends on it"
        teams = impact.teams_affected
        team_points = f["multi_team"]["points_per_additional_team"] * max(0, len(teams) - 1)
        return [
            self._factor("downstream_dependencies", "Downstream dependencies", downstream,
                         f"{n} downstream asset(s) depend on the affected data{pruned}"),
            self._factor("critical_downstream", "Critical consumers", critical_points,
                         f"{len(critical)} high/critical downstream asset(s)"
                         + (f", e.g. {', '.join(a.name for a in critical[:3])}" if critical else "")),
            self._factor("financial_reporting", "Financial reporting", f["financial_reporting"]["max_points"]
                         if impact.financial_reporting_affected else 0,
                         "Financial reporting depends on it" if impact.financial_reporting_affected
                         else "No financial reporting depends on it"),
            self._factor("executive_or_customer_facing", "Executive / customer-facing", exposure_points, exposure_reason),
            self._factor("multi_team", "Teams affected", team_points,
                         f"{len(teams)} team(s) affected: {', '.join(teams)}" if teams else "No owning team recorded"),
        ]

    # ------------------------------------------------------------------ results
    def band_for(self, score: float) -> str:
        return next(band for lower, band in self.bands if score >= lower)

    def score(self, signals: IssueSignals, impact: ImpactReport) -> PriorityResult:
        factors = self.defect_factors(signals) + self.blast_radius_factors(impact)
        total = round(min(100.0, sum(f.points for f in factors)), 1)
        return PriorityResult(score=total, band=self.band_for(total), factors=factors)

    def impact_score(self, impact: ImpactReport) -> tuple[float, str]:
        """Blast radius alone, normalised to 0-100, with a qualitative level."""
        factors = self.blast_radius_factors(impact)
        maximum = sum(f.max_points for f in factors)
        score = round(100 * sum(f.points for f in factors) / maximum, 1) if maximum else 0.0
        level = next((name for lower, name in self.impact_levels if score >= lower), "none")
        return score, level
