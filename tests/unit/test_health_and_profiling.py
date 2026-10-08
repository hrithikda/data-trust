from datetime import date, datetime

import pytest

from datatrust.profiling.profiler import (
    ColumnTarget,
    ProfileTarget,
    compile_stats_query,
    freshness,
    type_family,
)
from datatrust.quality.health import health_score, rule_score

REF = datetime(2026, 9, 30, 23, 59, 59)


def test_rule_score():
    assert rule_score("pass", 0.0) == 1.0
    assert rule_score("error", 0.3) is None
    assert rule_score("fail", 0.0001) == pytest.approx(0.499, abs=1e-3)  # any failure costs at least half
    assert rule_score("fail", 0.025) == 0.25
    assert rule_score("fail", 0.5) == 0.0


def test_health_is_severity_weighted_and_ignores_errors():
    assert health_score([("critical", 1.0), ("low", 0.0)]) == 80.0  # weights 4 and 1
    assert health_score([("low", 1.0), ("critical", 0.0)]) == 20.0
    assert health_score([("high", None)]) is None
    assert health_score([("high", None), ("medium", 0.5)]) == 50.0


@pytest.mark.parametrize(("data_type", "family"), [
    ("integer", "numeric"), ("numeric(12,2)", "numeric"), ("double precision", "numeric"),
    ("timestamp without time zone", "temporal"), ("date", "temporal"), ("boolean", "boolean"),
    ("character varying", "text"), (None, "text"),
])
def test_type_family(data_type, family):
    assert type_family(data_type) == family


def test_freshness_against_sla():
    assert freshness(None, REF, 6) == (None, None, "unknown")
    ts, lag, status = freshness(datetime(2026, 9, 30, 20, 0, 0), REF, 6)
    assert (lag, status) == (4.0, "fresh")
    assert freshness(datetime(2026, 9, 30, 12, 0, 0), REF, 6)[2] == "stale"
    assert freshness(datetime(2026, 9, 30, 12, 0, 0), REF, None)[2] == "unknown"
    assert freshness(date(2026, 9, 29), REF, 48)[0] == datetime(2026, 9, 29)


def test_pii_columns_get_no_value_statistics(settings):
    target = ProfileTarget(1, "customers", "analytics_staging", "customers", "loaded_at", 48, (
        ColumnTarget(1, "email", "text", contains_pii=True),
        ColumnTarget(2, "lifetime_value", "numeric", contains_pii=False),
    ))
    text = compile_stats_query(target).as_string(None)
    assert 'count(DISTINCT "email")' in text
    assert 'min("email")' not in text
    assert 'percentile_cont(0.5) WITHIN GROUP (ORDER BY "lifetime_value")' in text
    assert 'max("loaded_at") FILTER (WHERE "loaded_at" <= %(reference_ts)s)' in text
    assert '"analytics_staging"."customers"' in text
