"""Shared fixtures.

Database tests never touch the demo metadata: they create and drop their own scratch schemas
(``datatrust_test_*``) or, for the end-to-end test, a separate ``datatrust_test`` database.
Tests that need PostgreSQL are skipped when it is not reachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from psycopg import sql

from datatrust.config import REPO_ROOT, Settings
from datatrust.db import Database

CONFIG_DIR = REPO_ROOT / "config"
EVALUATION_DIR = REPO_ROOT / "evaluation"
DBT_TARGET_DIR = REPO_ROOT / "dbt" / "target"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def db(settings: Settings) -> Database:
    database = Database(settings)
    if not database.is_available():
        pytest.skip("PostgreSQL is not reachable (start it with `make db-up`)")
    return database


@pytest.fixture(scope="session")
def dbt_target_dir() -> Path:
    if not (DBT_TARGET_DIR / "manifest.json").exists():
        pytest.skip("dbt artifacts missing; run `make dbt` (or `make pipeline`) first")
    return DBT_TARGET_DIR


@pytest.fixture
def scratch_schema(db: Database) -> Iterator[str]:
    """An empty schema dropped after the test."""
    name = "datatrust_test_scratch"
    db.execute(sql.SQL("DROP SCHEMA IF EXISTS {s} CASCADE; CREATE SCHEMA {s}").format(s=sql.Identifier(name)))
    yield name
    db.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))
