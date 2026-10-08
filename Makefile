PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin
DT     := $(BIN)/datatrust

.PHONY: help install db-up db-down init-db generate dbt ingest profile quality evaluate pipeline status app \
        test test-unit test-db test-integration lint format screenshots clean

help:  ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

$(BIN)/datatrust:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

install: $(BIN)/datatrust  ## create the virtualenv and install DataTrust with dev tools
	@test -f .env || cp .env.example .env

db-up:  ## start PostgreSQL in Docker and wait until it accepts connections
	docker compose up -d --wait postgres

db-down:  ## stop PostgreSQL (data volume is kept)
	docker compose down

init-db: install  ## create the metadata schema (idempotent)
	$(DT) init-db

generate: install  ## generate synthetic source data with injected defects and load the raw schema
	$(DT) generate

dbt: install  ## dbt build (models + tests) and docs generate
	$(DT) dbt

ingest: install  ## ingest dbt artifacts and governance config into the catalog
	$(DT) ingest

profile: install  ## profile every catalogued relation
	$(DT) profile

quality: install  ## 14 daily quality runs ending at the reference date
	$(DT) quality backfill --days 14

evaluate: install  ## evaluate both detector versions on the held-out and regression suites
	$(DT) evaluate

pipeline: install  ## rebuild everything end to end (deterministic)
	$(DT) pipeline

status: install  ## show which steps have been run
	$(DT) status

app: install  ## run the Streamlit workbench on http://localhost:8501
	$(BIN)/streamlit run app/streamlit_app.py

test: install  ## all tests (database tests are skipped if PostgreSQL is unreachable)
	$(BIN)/pytest

test-unit: install  ## tests that need no database
	$(BIN)/pytest tests/unit

test-db: install  ## rule engine, profiler and evaluation against PostgreSQL
	$(BIN)/pytest tests/db

test-integration: install  ## full platform build in a throwaway database (~1 min)
	$(BIN)/pytest tests/integration

lint: install  ## ruff
	$(BIN)/ruff check datatrust app tests scripts

format: install  ## ruff autofix
	$(BIN)/ruff check --fix datatrust app tests scripts

screenshots: install  ## capture README screenshots from a running app (needs the docs extra)
	$(BIN)/pip install -e ".[docs]" && $(BIN)/playwright install chromium
	$(BIN)/python scripts/capture_screenshots.py

clean:  ## remove build artefacts (keeps the database)
	rm -rf dbt/target dbt/logs data/generated .pytest_cache .ruff_cache
