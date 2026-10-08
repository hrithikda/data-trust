"""Command-line entry point: ``datatrust <command>``.

Every step of the platform can be run individually; ``datatrust pipeline`` runs them all in
order and rebuilds the demo environment from scratch (it is deterministic, so rerunning it
produces the same warehouse, issues and evaluation results).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

from datatrust.config import Settings, get_settings
from datatrust.db import Database, json_default
from datatrust.errors import DataTrustError
from datatrust.logging_config import configure_logging

logger = logging.getLogger("datatrust.cli")

SUITES = ("holdout", "regression")
DETECTORS = ("baseline", "improved")


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, default=json_default))


# ---------------------------------------------------------------------- commands
def cmd_init_db(args: argparse.Namespace, settings: Settings, db: Database) -> None:
    from datatrust.metadata.schema import initialize_metadata_schema

    initialize_metadata_schema(db, reset=args.reset)


def cmd_generate(args: argparse.Namespace, settings: Settings, db: Database) -> None:
    from datatrust.generator.generate import generate_dataset
    from datatrust.generator.loader import load_raw, write_snapshot
    from datatrust.metadata.schema import record_pipeline_event

    seed = args.seed if args.seed is not None else settings.seed
    scale = args.scale if args.scale is not None else settings.scale
    dataset = generate_dataset(seed, scale, settings.reference_date, inject_defects=not args.clean)
    manifest = write_snapshot(dataset, settings.generated_data_dir)
    loaded = load_raw(dataset, db, settings.raw_schema)
    if db.table_exists("pipeline_events"):
        record_pipeline_event(db, "generate", "succeeded", {"seed": seed, "scale": scale, "clean": args.clean,
                                                            "rows": loaded, "defects": len(dataset.defects)})
    _print({"rows": loaded, "defects_injected": len(dataset.defects), "ground_truth": str(manifest)})


def cmd_dbt(args: argparse.Namespace, settings: Settings, db: Database) -> None:
    from datatrust.metadata.dbt_runner import build_warehouse
    from datatrust.metadata.schema import record_pipeline_event

    build_warehouse(settings)
    if db.table_exists("pipeline_events"):
        record_pipeline_event(db, "dbt_build", "succeeded", {"target": str(settings.dbt_target_dir)})


def cmd_ingest(args: argparse.Namespace, settings: Settings, db: Database) -> None:
    from datatrust.metadata.ingest import ingest_metadata

    _print(ingest_metadata(db, settings.config_dir, settings.dbt_target_dir).as_dict())


def cmd_profile(args: argparse.Namespace, settings: Settings, db: Database) -> None:
    from datatrust.profiling.profiler import Profiler

    summary = Profiler(db, settings).run(args.asset or None)
    _print(vars(summary))


def cmd_quality(args: argparse.Namespace, settings: Settings, db: Database) -> None:
    from datatrust.quality.service import QualityService

    service = QualityService(db, settings)
    if args.quality_command == "run":
        _print(vars(service.run(args.ruleset)))
    elif args.quality_command == "backfill":
        summaries = service.backfill(args.days, args.ruleset)
        _print([{"run_id": s.run_id, "as_of": s.as_of, "failed": s.rules_failed, "health": s.health_score}
                for s in summaries])
    elif args.quality_command == "sync-rules":
        _print({"rules": len(service.sync_rules())})


def cmd_evaluate(args: argparse.Namespace, settings: Settings, db: Database) -> None:
    from datatrust.evaluation.runner import EvaluationRunner

    runner = EvaluationRunner(db, settings)
    suites = SUITES if args.suite == "all" else (args.suite,)
    detectors = DETECTORS if args.detector == "all" else (args.detector,)
    results = []
    for suite in suites:
        for detector in detectors:
            result = runner.evaluate(suite, detector, persist=not args.dry_run)
            results.append({"suite": suite, "detector": detector, **result.matrix.as_dict(),
                            "errors": [f"{p.outcome} {p.case_id}: {p.title}" for p in result.errors()]})
    _print(results)


def cmd_issue(args: argparse.Namespace, settings: Settings, db: Database) -> None:
    from datatrust.quality.issues import add_issue_comment, change_issue_status

    if args.issue_command == "list":
        rows = db.fetch_all(
            "SELECT issue_key, status, priority_band, priority_score, affected_records, title FROM issues "
            "WHERE (%(all)s OR status <> 'resolved') ORDER BY priority_score DESC", {"all": args.all})
        for r in rows:
            print(f"{r['issue_key']}  {r['priority_band']} {float(r['priority_score']):5.1f}  "
                  f"{r['status']:<13} {r['title']}")
    elif args.issue_command == "status":
        change_issue_status(db, args.key, args.status, args.actor, args.note)
        print(f"{args.key} -> {args.status}")
    elif args.issue_command == "comment":
        add_issue_comment(db, args.key, args.actor, args.note)
        print(f"Comment added to {args.key}")


def cmd_triage(args: argparse.Namespace, settings: Settings, db: Database) -> None:
    from datatrust.quality.triage import apply_triage

    _print(apply_triage(db, args.file or settings.config_dir / "demo_triage.yml"))


def cmd_status(args: argparse.Namespace, settings: Settings, db: Database) -> None:
    from datatrust.services.overview import OverviewService

    _print(OverviewService(db).readiness().as_dict())


def cmd_pipeline(args: argparse.Namespace, settings: Settings, db: Database) -> None:
    """End-to-end rebuild. Deterministic: rerunning reproduces the same state."""
    from datatrust.metadata.schema import initialize_metadata_schema, record_pipeline_event

    steps: list[tuple[str, Callable[[], None]]] = [
        ("init-db", lambda: initialize_metadata_schema(db, reset=not args.keep_history)),
        ("generate", lambda: cmd_generate(argparse.Namespace(seed=None, scale=None, clean=False), settings, db)),
        ("dbt", lambda: cmd_dbt(args, settings, db)),
        ("ingest", lambda: cmd_ingest(args, settings, db)),
        ("profile", lambda: cmd_profile(argparse.Namespace(asset=None), settings, db)),
        ("quality", lambda: cmd_quality(argparse.Namespace(quality_command="backfill", days=args.days,
                                                           ruleset=None), settings, db)),
        ("triage", lambda: cmd_triage(argparse.Namespace(file=None), settings, db)),
        ("evaluate", lambda: cmd_evaluate(argparse.Namespace(suite="all", detector="all", dry_run=False),
                                          settings, db)),
    ]
    for name, step in steps:
        started = time.perf_counter()
        logger.info("== %s", name)
        try:
            step()
        except DataTrustError:
            if db.table_exists("pipeline_events"):
                record_pipeline_event(db, f"pipeline:{name}", "failed")
            raise
        logger.info("== %s done in %.1fs", name, time.perf_counter() - started)
    record_pipeline_event(db, "pipeline", "succeeded", {"days": args.days})


# ---------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="datatrust", description="DataTrust data quality and lineage workbench")
    parser.add_argument("--log-level", help="override DATATRUST_LOG_LEVEL")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create the database and metadata schema (idempotent)")
    p.add_argument("--reset", action="store_true", help="drop the metadata schema first (discards history)")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("generate", help="generate synthetic source data and load the raw schema")
    p.add_argument("--seed", type=int)
    p.add_argument("--scale", type=float)
    p.add_argument("--clean", action="store_true", help="skip defect injection")
    p.set_defaults(func=cmd_generate)

    sub.add_parser("dbt", help="dbt build + docs generate").set_defaults(func=cmd_dbt)
    sub.add_parser("ingest", help="ingest dbt artifacts and governance config").set_defaults(func=cmd_ingest)

    p = sub.add_parser("profile", help="profile catalogued relations")
    p.add_argument("--asset", action="append", help="limit to an asset (repeatable)")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("quality", help="run quality rules")
    qsub = p.add_subparsers(dest="quality_command", required=True)
    q = qsub.add_parser("run", help="one run at the reference date")
    q.add_argument("--ruleset", choices=DETECTORS)
    q = qsub.add_parser("backfill", help="daily runs over the last N days (uses row load times)")
    q.add_argument("--days", type=int, default=14)
    q.add_argument("--ruleset", choices=DETECTORS)
    qsub.add_parser("sync-rules", help="validate and store rule definitions")
    p.set_defaults(func=cmd_quality)

    p = sub.add_parser("evaluate", help="measure detector versions on labelled cases")
    p.add_argument("--suite", choices=(*SUITES, "all"), default="all")
    p.add_argument("--detector", choices=(*DETECTORS, "all"), default="all")
    p.add_argument("--dry-run", action="store_true", help="do not persist results")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("issue", help="list and triage quality issues")
    isub = p.add_subparsers(dest="issue_command", required=True)
    i = isub.add_parser("list")
    i.add_argument("--all", action="store_true", help="include resolved issues")
    i = isub.add_parser("status")
    i.add_argument("key")
    i.add_argument("status", choices=("open", "investigating", "accepted", "resolved"))
    i.add_argument("--actor", required=True)
    i.add_argument("--note")
    i = isub.add_parser("comment")
    i.add_argument("key")
    i.add_argument("--actor", required=True)
    i.add_argument("--note", required=True)
    p.set_defaults(func=cmd_issue)

    p = sub.add_parser("triage", help="apply scripted triage actions")
    p.add_argument("--file", type=Path)
    p.set_defaults(func=cmd_triage)

    sub.add_parser("status", help="show what has been built so far").set_defaults(func=cmd_status)

    p = sub.add_parser("pipeline", help="run every step end to end")
    p.add_argument("--days", type=int, default=14, help="quality backfill window")
    p.add_argument("--keep-history", action="store_true", help="do not reset the metadata schema")
    p.set_defaults(func=cmd_pipeline)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()
    except ValueError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2
    configure_logging(args.log_level or settings.log_level)
    db = Database(settings)
    try:
        args.func(args, settings, db)
    except DataTrustError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
