-- DataTrust metadata model.
-- Executed with search_path set to the metadata schema, so objects are unqualified.
-- Every statement is idempotent: re-running `datatrust init-db` is safe.

-- ---------------------------------------------------------------------------
-- Governance: teams, source systems, business glossary
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teams (
    team_id         serial PRIMARY KEY,
    team_key        text NOT NULL UNIQUE CHECK (team_key ~ '^[a-z][a-z0-9_]*$'),
    name            text NOT NULL,
    description     text NOT NULL,
    lead_name       text NOT NULL,
    email           text NOT NULL,
    slack_channel   text NOT NULL,
    on_call_rotation text
);

CREATE TABLE IF NOT EXISTS source_systems (
    source_system_id serial PRIMARY KEY,
    system_key      text NOT NULL UNIQUE CHECK (system_key ~ '^[a-z][a-z0-9_]*$'),
    name            text NOT NULL,
    system_type     text NOT NULL,
    vendor          text NOT NULL,
    description     text NOT NULL,
    owner_team_id   integer NOT NULL REFERENCES teams (team_id),
    ingestion_method text NOT NULL,
    load_frequency  text NOT NULL
);

-- ---------------------------------------------------------------------------
-- Technical metadata: assets, columns, lineage (sourced from dbt artifacts)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assets (
    asset_id        serial PRIMARY KEY,
    unique_id       text NOT NULL UNIQUE,               -- dbt unique_id, e.g. model.datatrust_warehouse.fct_orders
    name            text NOT NULL UNIQUE,               -- logical name used across DataTrust
    asset_type      text NOT NULL CHECK (asset_type IN ('source', 'model', 'exposure')),
    layer           text NOT NULL CHECK (layer IN ('raw', 'staging', 'intermediate', 'mart', 'exposure')),
    database_name   text,
    schema_name     text,
    relation_name   text,                               -- fully qualified, quoted relation (NULL for exposures)
    description     text NOT NULL DEFAULT '',
    domain          text NOT NULL,
    owner_team_id   integer REFERENCES teams (team_id),
    criticality     text NOT NULL CHECK (criticality IN ('low', 'medium', 'high', 'critical')),
    materialization text,
    exposure_type   text,                               -- dashboard / application / analysis (exposures only)
    audience        text,                               -- executive / finance / customer_facing / internal
    is_financial_reporting boolean NOT NULL DEFAULT false,
    is_customer_facing boolean NOT NULL DEFAULT false,
    contains_pii    boolean NOT NULL DEFAULT false,
    freshness_column text,
    freshness_sla_hours integer CHECK (freshness_sla_hours > 0),
    primary_key     text[] NOT NULL DEFAULT '{}',
    tags            text[] NOT NULL DEFAULT '{}',
    file_path       text,
    url             text,
    is_active       boolean NOT NULL DEFAULT true,      -- false once removed from the dbt project
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_assets_layer ON assets (layer);
CREATE INDEX IF NOT EXISTS ix_assets_owner ON assets (owner_team_id);
CREATE INDEX IF NOT EXISTS ix_assets_domain ON assets (domain);

CREATE TABLE IF NOT EXISTS asset_columns (
    column_id       serial PRIMARY KEY,
    asset_id        integer NOT NULL REFERENCES assets (asset_id) ON DELETE CASCADE,
    column_name     text NOT NULL,
    ordinal_position integer NOT NULL,
    data_type       text,
    description     text NOT NULL DEFAULT '',
    is_critical     boolean NOT NULL DEFAULT false,
    cde_category    text CHECK (cde_category IN ('identifier', 'customer_identifier', 'financial', 'temporal', 'status')),
    critical_reason text,
    contains_pii    boolean NOT NULL DEFAULT false,
    is_active       boolean NOT NULL DEFAULT true,
    UNIQUE (asset_id, column_name),
    CHECK (NOT is_critical OR cde_category IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_asset_columns_critical ON asset_columns (asset_id) WHERE is_critical;
CREATE INDEX IF NOT EXISTS ix_asset_columns_name ON asset_columns (column_name);

CREATE TABLE IF NOT EXISTS asset_dependencies (
    upstream_asset_id   integer NOT NULL REFERENCES assets (asset_id) ON DELETE CASCADE,
    downstream_asset_id integer NOT NULL REFERENCES assets (asset_id) ON DELETE CASCADE,
    dependency_type     text NOT NULL CHECK (dependency_type IN ('ref', 'source', 'exposure')),
    -- Upstream columns the downstream model's compiled SQL references; NULL = unknown (treat as all).
    referenced_columns  text[],
    PRIMARY KEY (upstream_asset_id, downstream_asset_id),
    CHECK (upstream_asset_id <> downstream_asset_id)
);
CREATE INDEX IF NOT EXISTS ix_asset_dependencies_downstream ON asset_dependencies (downstream_asset_id);

-- Raw source tables trace back to the operational system that produced them.
CREATE TABLE IF NOT EXISTS source_mappings (
    mapping_id       serial PRIMARY KEY,
    asset_id         integer NOT NULL UNIQUE REFERENCES assets (asset_id) ON DELETE CASCADE,
    source_system_id integer NOT NULL REFERENCES source_systems (source_system_id),
    source_object    text NOT NULL,                     -- object/endpoint in the operational system
    extraction_notes text
);

CREATE TABLE IF NOT EXISTS glossary_terms (
    term_id         serial PRIMARY KEY,
    term_key        text NOT NULL UNIQUE CHECK (term_key ~ '^[a-z][a-z0-9_]*$'),
    name            text NOT NULL UNIQUE,
    definition      text NOT NULL,
    calculation     text,
    domain          text NOT NULL,
    owner_team_id   integer NOT NULL REFERENCES teams (team_id),
    steward         text NOT NULL,
    status          text NOT NULL CHECK (status IN ('draft', 'approved', 'deprecated')),
    synonyms        text[] NOT NULL DEFAULT '{}',
    related_term_keys text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS glossary_term_links (
    link_id     serial PRIMARY KEY,
    term_id     integer NOT NULL REFERENCES glossary_terms (term_id) ON DELETE CASCADE,
    asset_id    integer NOT NULL REFERENCES assets (asset_id) ON DELETE CASCADE,
    column_id   integer REFERENCES asset_columns (column_id) ON DELETE CASCADE,
    UNIQUE NULLS NOT DISTINCT (term_id, asset_id, column_id)
);
CREATE INDEX IF NOT EXISTS ix_glossary_links_asset ON glossary_term_links (asset_id);

-- dbt tests and their latest outcome from run_results.json
CREATE TABLE IF NOT EXISTS dbt_tests (
    dbt_test_id     serial PRIMARY KEY,
    unique_id       text NOT NULL UNIQUE,
    asset_id        integer REFERENCES assets (asset_id) ON DELETE CASCADE,
    column_name     text,
    test_name       text NOT NULL,
    severity        text NOT NULL,
    last_status     text,
    last_failures   integer,
    last_run_at     timestamptz
);
CREATE INDEX IF NOT EXISTS ix_dbt_tests_asset ON dbt_tests (asset_id);

-- ---------------------------------------------------------------------------
-- Profiling
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS profile_runs (
    profile_run_id  serial PRIMARY KEY,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    asset_count     integer NOT NULL DEFAULT 0,
    error_message   text
);

CREATE TABLE IF NOT EXISTS asset_profiles (
    asset_profile_id serial PRIMARY KEY,
    profile_run_id  integer NOT NULL REFERENCES profile_runs (profile_run_id) ON DELETE CASCADE,
    asset_id        integer NOT NULL REFERENCES assets (asset_id) ON DELETE CASCADE,
    row_count       bigint NOT NULL CHECK (row_count >= 0),
    column_count    integer NOT NULL,
    freshest_value  timestamp,
    freshness_lag_hours numeric(12, 2),
    freshness_status text CHECK (freshness_status IN ('fresh', 'stale', 'unknown')),
    duration_ms     integer NOT NULL,
    profiled_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (profile_run_id, asset_id)
);
CREATE INDEX IF NOT EXISTS ix_asset_profiles_asset ON asset_profiles (asset_id, profiled_at DESC);

CREATE TABLE IF NOT EXISTS column_profiles (
    column_profile_id serial PRIMARY KEY,
    asset_profile_id integer NOT NULL REFERENCES asset_profiles (asset_profile_id) ON DELETE CASCADE,
    column_id       integer NOT NULL REFERENCES asset_columns (column_id) ON DELETE CASCADE,
    null_count      bigint NOT NULL,
    null_rate       numeric(7, 6) NOT NULL CHECK (null_rate BETWEEN 0 AND 1),
    distinct_count  bigint NOT NULL,
    uniqueness      numeric(7, 6) NOT NULL CHECK (uniqueness BETWEEN 0 AND 1),
    min_value       text,
    max_value       text,
    mean_value      double precision,
    stddev_value    double precision,
    median_value    double precision,
    top_values      jsonb NOT NULL DEFAULT '[]',
    sample_values   jsonb NOT NULL DEFAULT '[]',
    UNIQUE (asset_profile_id, column_id)
);

-- ---------------------------------------------------------------------------
-- Data quality: rules, runs, results, issues
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quality_rules (
    rule_id         serial PRIMARY KEY,
    rule_key        text NOT NULL UNIQUE CHECK (rule_key ~ '^[a-z][a-z0-9_]*$'),
    name            text NOT NULL,
    description     text NOT NULL,
    category        text NOT NULL,
    rule_type       text NOT NULL,
    asset_id        integer NOT NULL REFERENCES assets (asset_id),
    column_names    text[] NOT NULL DEFAULT '{}',
    severity        text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    params          jsonb NOT NULL DEFAULT '{}',
    business_impact text NOT NULL,
    rulesets        text[] NOT NULL,
    supersedes      text,
    rationale       text,
    is_active       boolean NOT NULL DEFAULT true,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_quality_rules_asset ON quality_rules (asset_id);

CREATE TABLE IF NOT EXISTS quality_runs (
    run_id          serial PRIMARY KEY,
    ruleset         text NOT NULL,
    as_of           timestamp NOT NULL,                 -- data cutoff the rules evaluated
    trigger         text NOT NULL CHECK (trigger IN ('manual', 'scheduled', 'backfill', 'test')),
    status          text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    rules_executed  integer NOT NULL DEFAULT 0,
    rules_failed    integer NOT NULL DEFAULT 0,
    rules_errored   integer NOT NULL DEFAULT 0,
    health_score    numeric(5, 2)
);
CREATE INDEX IF NOT EXISTS ix_quality_runs_as_of ON quality_runs (as_of DESC);

CREATE TABLE IF NOT EXISTS quality_results (
    result_id       serial PRIMARY KEY,
    run_id          integer NOT NULL REFERENCES quality_runs (run_id) ON DELETE CASCADE,
    rule_id         integer NOT NULL REFERENCES quality_rules (rule_id),
    asset_id        integer NOT NULL REFERENCES assets (asset_id),
    status          text NOT NULL CHECK (status IN ('pass', 'fail', 'error')),
    records_scanned bigint NOT NULL DEFAULT 0 CHECK (records_scanned >= 0),
    records_failed  bigint NOT NULL DEFAULT 0 CHECK (records_failed >= 0),
    failure_rate    numeric(9, 6) NOT NULL DEFAULT 0,
    sample_failures jsonb NOT NULL DEFAULT '[]',
    critical_columns text[] NOT NULL DEFAULT '{}',
    downstream_count integer NOT NULL DEFAULT 0,
    severity_weight smallint NOT NULL CHECK (severity_weight BETWEEN 1 AND 4),
    rule_score      numeric(5, 4) CHECK (rule_score BETWEEN 0 AND 1),  -- NULL for errored rules
    execution_ms    integer NOT NULL DEFAULT 0,
    error_message   text,
    executed_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, rule_id)
);
CREATE INDEX IF NOT EXISTS ix_quality_results_rule ON quality_results (rule_id, run_id DESC);
CREATE INDEX IF NOT EXISTS ix_quality_results_asset ON quality_results (asset_id, run_id DESC);

CREATE TABLE IF NOT EXISTS issues (
    issue_id        serial PRIMARY KEY,
    issue_key       text NOT NULL UNIQUE,
    rule_id         integer NOT NULL REFERENCES quality_rules (rule_id),
    asset_id        integer NOT NULL REFERENCES assets (asset_id),
    owner_team_id   integer REFERENCES teams (team_id),
    title           text NOT NULL,
    description     text NOT NULL,
    status          text NOT NULL CHECK (status IN ('open', 'investigating', 'resolved', 'accepted')),
    severity        text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    business_criticality text NOT NULL,
    affected_records bigint NOT NULL CHECK (affected_records >= 0),
    affected_rate   numeric(9, 6) NOT NULL,
    records_scanned bigint NOT NULL,
    sample_failures jsonb NOT NULL DEFAULT '[]',
    critical_columns text[] NOT NULL DEFAULT '{}',
    priority_score  numeric(5, 1) NOT NULL CHECK (priority_score BETWEEN 0 AND 100),
    priority_band   text NOT NULL CHECK (priority_band IN ('P1', 'P2', 'P3', 'P4')),
    priority_breakdown jsonb NOT NULL DEFAULT '[]',
    impact_summary  jsonb NOT NULL DEFAULT '{}',
    first_run_id    integer NOT NULL REFERENCES quality_runs (run_id),
    last_run_id     integer NOT NULL REFERENCES quality_runs (run_id),
    first_detected_at timestamp NOT NULL,
    last_detected_at timestamp NOT NULL,
    occurrences     integer NOT NULL DEFAULT 1,
    resolved_at     timestamp,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
-- At most one unresolved issue per rule: repeated failures update it instead of duplicating it.
CREATE UNIQUE INDEX IF NOT EXISTS ux_issues_one_unresolved_per_rule
    ON issues (rule_id) WHERE status IN ('open', 'investigating', 'accepted');
CREATE INDEX IF NOT EXISTS ix_issues_priority ON issues (priority_score DESC);
CREATE INDEX IF NOT EXISTS ix_issues_status ON issues (status);

CREATE TABLE IF NOT EXISTS issue_impacts (
    issue_id        integer NOT NULL REFERENCES issues (issue_id) ON DELETE CASCADE,
    impacted_asset_id integer NOT NULL REFERENCES assets (asset_id) ON DELETE CASCADE,
    depth           integer NOT NULL CHECK (depth >= 1),
    is_critical     boolean NOT NULL,
    path            text[] NOT NULL,
    PRIMARY KEY (issue_id, impacted_asset_id)
);

CREATE TABLE IF NOT EXISTS issue_events (
    event_id        serial PRIMARY KEY,
    issue_id        integer NOT NULL REFERENCES issues (issue_id) ON DELETE CASCADE,
    event_type      text NOT NULL CHECK (event_type IN ('created', 'redetected', 'status_change', 'auto_resolved', 'reopened', 'comment')),
    from_status     text,
    to_status       text,
    actor           text NOT NULL,
    note            text,
    run_id          integer REFERENCES quality_runs (run_id),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_issue_events_issue ON issue_events (issue_id, created_at);

-- ---------------------------------------------------------------------------
-- Detection evaluation (held-out cases; runs are append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evaluation_cases (
    case_id         text PRIMARY KEY,
    suite           text NOT NULL CHECK (suite IN ('holdout', 'regression')),
    title           text NOT NULL,
    description     text NOT NULL,
    expected_label  text NOT NULL CHECK (expected_label IN ('defective', 'valid')),
    defect_category text,
    payload         jsonb NOT NULL,
    content_hash    text NOT NULL,
    CHECK ((expected_label = 'defective') = (defect_category IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    eval_run_id     serial PRIMARY KEY,
    suite           text NOT NULL,
    detector_version text NOT NULL CHECK (detector_version IN ('baseline', 'improved')),
    case_count      integer NOT NULL,
    true_positives  integer NOT NULL,
    false_negatives integer NOT NULL,
    false_positives integer NOT NULL,
    true_negatives  integer NOT NULL,
    accuracy        numeric(6, 4),
    precision_score numeric(6, 4),
    recall          numeric(6, 4),
    f1_score        numeric(6, 4),
    specificity     numeric(6, 4),
    cases_hash      text NOT NULL,
    executed_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (true_positives + false_negatives + false_positives + true_negatives = case_count)
);

CREATE TABLE IF NOT EXISTS evaluation_predictions (
    eval_run_id     integer NOT NULL REFERENCES evaluation_runs (eval_run_id) ON DELETE CASCADE,
    case_id         text NOT NULL REFERENCES evaluation_cases (case_id),
    expected_label  text NOT NULL,
    predicted_label text NOT NULL CHECK (predicted_label IN ('defective', 'valid')),
    outcome         text NOT NULL CHECK (outcome IN ('TP', 'FN', 'FP', 'TN')),
    triggered_rules text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (eval_run_id, case_id)
);

-- ---------------------------------------------------------------------------
-- Pipeline bookkeeping (lets the UI explain what has / has not been run)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_events (
    event_id        serial PRIMARY KEY,
    step            text NOT NULL,
    status          text NOT NULL CHECK (status IN ('succeeded', 'failed')),
    details         jsonb NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_pipeline_events_step ON pipeline_events (step, created_at DESC);
