"""Parse dbt artifacts (manifest.json, catalog.json, run_results) into DataTrust metadata.

The manifest is the source of truth for assets, descriptions, ownership meta, critical
fields and lineage (``depends_on``). catalog.json contributes physical column types and
undocumented columns; run_results contributes the latest dbt test outcomes.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from datatrust.errors import ArtifactsMissingError, ConfigurationError

logger = logging.getLogger(__name__)

MODEL_META_KEYS = {"owner", "domain", "criticality", "primary_key", "freshness_column", "freshness_sla_hours",
                   "loaded_at_column", "contains_pii"}
SOURCE_META_KEYS = {"source_system", "owner", "domain", "source_object", "criticality", "contains_pii"}
EXPOSURE_META_KEYS = {"owner", "criticality", "audience", "financial_reporting", "customer_facing"}
COLUMN_META_KEYS = {"critical", "cde_category", "critical_reason", "pii"}
CRITICALITIES = {"low", "medium", "high", "critical"}
CDE_CATEGORIES = {"identifier", "customer_identifier", "financial", "temporal", "status"}
LAYER_BY_FOLDER = {"staging": "staging", "intermediate": "intermediate", "marts": "mart"}
BUILD_RESULTS_FILE = "build_run_results.json"


@dataclass
class ColumnRecord:
    name: str
    ordinal: int
    data_type: str | None
    description: str = ""
    is_critical: bool = False
    cde_category: str | None = None
    critical_reason: str | None = None
    contains_pii: bool = False


@dataclass
class AssetRecord:
    unique_id: str
    name: str
    asset_type: str
    layer: str
    domain: str
    owner: str | None
    criticality: str
    description: str = ""
    database: str | None = None
    schema: str | None = None
    relation_name: str | None = None
    materialization: str | None = None
    exposure_type: str | None = None
    audience: str | None = None
    is_financial_reporting: bool = False
    is_customer_facing: bool = False
    contains_pii: bool = False
    freshness_column: str | None = None
    freshness_sla_hours: int | None = None
    loaded_at_column: str | None = None
    primary_key: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    file_path: str | None = None
    url: str | None = None
    source_system: str | None = None
    source_object: str | None = None
    columns: list[ColumnRecord] = field(default_factory=list)


@dataclass
class DependencyRecord:
    upstream: str  # unique_id
    downstream: str
    dependency_type: str
    referenced_columns: list[str] | None = None  # None = unknown, treat as every column


@dataclass
class DbtTestRecord:
    unique_id: str
    attached_to: str | None
    column_name: str | None
    test_name: str
    severity: str
    status: str | None = None
    failures: int | None = None
    executed_at: str | None = None


@dataclass
class DbtProject:
    dbt_version: str
    generated_at: str
    assets: list[AssetRecord]
    dependencies: list[DependencyRecord]
    tests: list[DbtTestRecord]

    def asset(self, name: str) -> AssetRecord | None:
        return next((a for a in self.assets if a.name == name), None)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactsMissingError(f"{path} is not valid JSON ({exc}); re-run `make dbt`.") from exc


def _meta(node: dict[str, Any]) -> dict[str, Any]:
    """dbt >= 1.10 stores meta under config; older versions at the top level. Merge both."""
    merged = dict(node.get("meta") or {})
    merged.update((node.get("config") or {}).get("meta") or {})
    return merged


def _check_keys(meta: dict[str, Any], allowed: set[str], where: str, problems: list[str]) -> None:
    unknown = set(meta) - allowed
    if unknown:
        problems.append(f"{where}: unknown meta keys {sorted(unknown)}")


def _criticality(value: Any, where: str, problems: list[str]) -> str:
    if value not in CRITICALITIES:
        problems.append(f"{where}: criticality must be one of {sorted(CRITICALITIES)}, got {value!r}")
        return "medium"
    return value


HOURS_PER_PERIOD = {"minute": 1 / 60, "hour": 1, "day": 24}


def _source_sla_hours(source: dict[str, Any], where: str, problems: list[str]) -> int | None:
    """Convert dbt source freshness ``warn_after`` into whole hours (None when undeclared)."""
    freshness = source.get("freshness") or (source.get("config") or {}).get("freshness") or {}
    warn_after = freshness.get("warn_after") or {}
    count, period = warn_after.get("count"), warn_after.get("period")
    if count is None or period is None:
        return None
    if period not in HOURS_PER_PERIOD:
        problems.append(f"{where}: unsupported freshness period {period!r}")
        return None
    return max(1, round(count * HOURS_PER_PERIOD[period]))


def _columns(node: dict[str, Any], catalog_columns: dict[str, Any], where: str, problems: list[str]) -> list[ColumnRecord]:
    documented = node.get("columns") or {}
    names = sorted(catalog_columns, key=lambda c: catalog_columns[c].get("index", 0)) if catalog_columns else list(documented)
    names += [c for c in documented if c not in names]
    records: list[ColumnRecord] = []
    for ordinal, name in enumerate(names, start=1):
        doc = documented.get(name, {})
        meta = _meta(doc)
        _check_keys(meta, COLUMN_META_KEYS, f"{where}.{name}", problems)
        is_critical = bool(meta.get("critical", False))
        category = meta.get("cde_category")
        if is_critical and category not in CDE_CATEGORIES:
            problems.append(f"{where}.{name}: critical columns need cde_category in {sorted(CDE_CATEGORIES)}")
        catalog_type = (catalog_columns.get(name) or {}).get("type")
        records.append(ColumnRecord(
            name=name, ordinal=ordinal, data_type=catalog_type or doc.get("data_type"),
            description=" ".join((doc.get("description") or "").split()),
            is_critical=is_critical, cde_category=category if is_critical else None,
            critical_reason=meta.get("critical_reason"), contains_pii=bool(meta.get("pii", False)),
        ))
    return records


def parse_artifacts(target_dir: Path) -> DbtProject:
    """Parse the dbt target directory. Raises ArtifactsMissingError / ConfigurationError."""
    manifest_path = target_dir / "manifest.json"
    if not manifest_path.exists():
        raise ArtifactsMissingError(f"dbt manifest not found at {manifest_path}. Run `make dbt` first.")
    manifest = _load_json(manifest_path)
    catalog_path = target_dir / "catalog.json"
    catalog = _load_json(catalog_path) if catalog_path.exists() else {"nodes": {}, "sources": {}}
    if not catalog_path.exists():
        logger.warning("catalog.json missing; column types fall back to declared contracts")

    problems: list[str] = []
    assets: list[AssetRecord] = []
    dependencies: list[DependencyRecord] = []

    for uid, source in sorted(manifest.get("sources", {}).items()):
        meta = _meta(source)
        _check_keys(meta, SOURCE_META_KEYS, uid, problems)
        assets.append(AssetRecord(
            unique_id=uid, name=source["identifier"], asset_type="source", layer="raw",
            domain=meta.get("domain", "platform"), owner=meta.get("owner"),
            criticality=_criticality(meta.get("criticality", "medium"), uid, problems),
            description=" ".join((source.get("description") or source.get("source_description") or "").split()),
            database=source.get("database"), schema=source.get("schema"), relation_name=source.get("relation_name"),
            contains_pii=bool(meta.get("contains_pii", False)), loaded_at_column=source.get("loaded_at_field"),
            freshness_column=source.get("loaded_at_field"), freshness_sla_hours=_source_sla_hours(source, uid, problems),
            tags=list(source.get("tags") or []), file_path=source.get("original_file_path"),
            source_system=meta.get("source_system"), source_object=meta.get("source_object"),
            columns=_columns(source, (catalog["sources"].get(uid) or {}).get("columns", {}), uid, problems),
        ))

    for uid, node in sorted(manifest.get("nodes", {}).items()):
        if node.get("resource_type") != "model":
            continue
        meta = _meta(node)
        _check_keys(meta, MODEL_META_KEYS, uid, problems)
        folder = node["fqn"][1] if len(node.get("fqn", [])) > 2 else ""
        layer = LAYER_BY_FOLDER.get(folder)
        if layer is None:
            problems.append(f"{uid}: models must live under staging/, intermediate/ or marts/")
            continue
        domain = meta.get("domain", "unknown")
        assets.append(AssetRecord(
            unique_id=uid, name=node["name"], asset_type="model", layer=layer, domain=domain,
            owner=meta.get("owner"), criticality=_criticality(meta.get("criticality", "medium"), uid, problems),
            description=" ".join((node.get("description") or "").split()),
            database=node.get("database"), schema=node.get("schema"), relation_name=node.get("relation_name"),
            materialization=(node.get("config") or {}).get("materialized"),
            is_financial_reporting=domain in {"finance", "executive"},
            contains_pii=bool(meta.get("contains_pii", False)),
            freshness_column=meta.get("freshness_column"), freshness_sla_hours=meta.get("freshness_sla_hours"),
            loaded_at_column=meta.get("loaded_at_column"), primary_key=list(meta.get("primary_key") or []),
            tags=list(node.get("tags") or []), file_path=node.get("original_file_path"),
            columns=_columns(node, (catalog["nodes"].get(uid) or {}).get("columns", {}), uid, problems),
        ))
        dependencies.extend(
            DependencyRecord(parent, uid, "source" if parent.startswith("source.") else "ref")
            for parent in sorted(set(node.get("depends_on", {}).get("nodes", [])))
        )

    domains = {a.unique_id: a.domain for a in assets}
    for uid, exposure in sorted(manifest.get("exposures", {}).items()):
        meta = _meta(exposure)
        _check_keys(meta, EXPOSURE_META_KEYS, uid, problems)
        parents = sorted(set(exposure.get("depends_on", {}).get("nodes", [])))
        assets.append(AssetRecord(
            unique_id=uid, name=exposure["name"], asset_type="exposure", layer="exposure",
            domain=domains.get(parents[0], "unknown") if parents else "unknown", owner=meta.get("owner"),
            criticality=_criticality(meta.get("criticality", "medium"), uid, problems),
            description=" ".join((exposure.get("description") or "").split()),
            exposure_type=exposure.get("type"), audience=meta.get("audience"),
            is_financial_reporting=bool(meta.get("financial_reporting", False)),
            is_customer_facing=bool(meta.get("customer_facing", False)),
            tags=list(exposure.get("tags") or []), file_path=exposure.get("original_file_path"), url=exposure.get("url"),
        ))
        dependencies.extend(DependencyRecord(parent, uid, "exposure") for parent in parents)

    if problems:
        raise ConfigurationError("Invalid dbt metadata:\n  - " + "\n  - ".join(problems))

    _annotate_column_references(dependencies, assets, manifest)
    return DbtProject(
        dbt_version=manifest.get("metadata", {}).get("dbt_version", "unknown"),
        generated_at=manifest.get("metadata", {}).get("generated_at", ""),
        assets=assets, dependencies=dependencies, tests=_tests(manifest, target_dir),
    )


def _annotate_column_references(dependencies: list[DependencyRecord], assets: list[AssetRecord],
                                manifest: dict[str, Any]) -> None:
    """Record which upstream columns each downstream model's compiled SQL mentions.

    A whole-word match is deliberately conservative: it can over-report a reference (same
    column name from another table) but never misses a real one. Exposures and models
    without compiled SQL keep ``None`` (every column treated as referenced).
    """
    columns_by_uid = {a.unique_id: [c.name for c in a.columns] for a in assets}
    for dep in dependencies:
        node = manifest.get("nodes", {}).get(dep.downstream)
        code = (node or {}).get("compiled_code")
        if dep.dependency_type == "exposure" or not code:
            continue
        dep.referenced_columns = [c for c in columns_by_uid.get(dep.upstream, [])
                                  if re.search(rf"\b{re.escape(c)}\b", code)]


def _tests(manifest: dict[str, Any], target_dir: Path) -> list[DbtTestRecord]:
    outcomes: dict[str, dict[str, Any]] = {}
    executed_at = None
    for candidate in (target_dir / BUILD_RESULTS_FILE, target_dir / "run_results.json"):
        if candidate.exists():
            results = _load_json(candidate)
            tests = {r["unique_id"]: r for r in results.get("results", []) if r["unique_id"].startswith("test.")}
            if tests:
                outcomes, executed_at = tests, results.get("metadata", {}).get("generated_at")
                break
    records: list[DbtTestRecord] = []
    for uid, node in sorted(manifest.get("nodes", {}).items()):
        if node.get("resource_type") != "test":
            continue
        test_meta = node.get("test_metadata") or {}
        attached = node.get("attached_node") or next(iter(node.get("depends_on", {}).get("nodes", [])), None)
        outcome = outcomes.get(uid, {})
        records.append(DbtTestRecord(
            unique_id=uid, attached_to=attached, column_name=node.get("column_name"),
            test_name=test_meta.get("name") or node["name"],
            severity=str((node.get("config") or {}).get("severity", "error")).lower(),
            status=outcome.get("status"), failures=outcome.get("failures"),
            executed_at=executed_at if outcome else None,
        ))
    return records
