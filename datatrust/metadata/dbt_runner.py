"""Run dbt with DataTrust's configuration and keep the artifacts DataTrust ingests."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from datatrust.config import Settings
from datatrust.errors import DataTrustError
from datatrust.metadata.dbt_artifacts import BUILD_RESULTS_FILE

logger = logging.getLogger(__name__)


def _dbt_executable() -> str:
    """Prefer the dbt installed next to the running interpreter (the project venv)."""
    local = Path(sys.executable).parent / "dbt"
    if local.exists():
        return str(local)
    found = shutil.which("dbt")
    if not found:
        raise DataTrustError("dbt executable not found. Install the project with `pip install -e .`.")
    return found


def run_dbt(settings: Settings, *args: str) -> None:
    """Run one dbt command against the configured database; raises on failure."""
    command = [_dbt_executable(), *args, "--project-dir", str(settings.dbt_project_dir),
               "--profiles-dir", str(settings.dbt_project_dir)]
    env = {**os.environ, **settings.dbt_env()}
    logger.info("Running: dbt %s", " ".join(args))
    completed = subprocess.run(command, env=env, cwd=settings.dbt_project_dir, capture_output=True, text=True)
    for line in completed.stdout.splitlines():
        if any(marker in line for marker in ("Done.", "ERROR", "Completed", "Catalog written")):
            logger.info("dbt | %s", line.strip())
    if completed.returncode != 0:
        tail = "\n".join((completed.stdout + completed.stderr).splitlines()[-30:])
        raise DataTrustError(f"dbt {' '.join(args)} failed (exit {completed.returncode}):\n{tail}")


def build_warehouse(settings: Settings) -> None:
    """dbt build (models + tests), preserve its run_results, then generate the catalog."""
    run_dbt(settings, "build")
    target = settings.dbt_target_dir
    shutil.copyfile(target / "run_results.json", target / BUILD_RESULTS_FILE)
    run_dbt(settings, "docs", "generate")
