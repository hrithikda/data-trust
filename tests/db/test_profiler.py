import pytest
from psycopg import sql

from datatrust.errors import DataTrustError
from datatrust.profiling.profiler import ColumnTarget, Profiler, ProfileTarget

pytestmark = pytest.mark.db


@pytest.fixture
def target(db, scratch_schema) -> ProfileTarget:
    db.execute(sql.SQL("""
        CREATE TABLE {t} (id integer, email text, channel text, amount numeric, loaded_at timestamp);
        INSERT INTO {t} VALUES
          (1, 'a@x.com', 'web', 10.0, '2026-09-30 20:00'),
          (2, NULL,      'web', 20.0, '2026-09-30 21:00'),
          (3, 'c@x.com', 'app', NULL, '2026-09-29 08:00'),
          (4, 'd@x.com', 'web', 30.0, '2026-10-02 08:00');  -- loaded after the snapshot: ignored for freshness
    """).format(t=sql.Identifier(scratch_schema, "events")))
    return ProfileTarget(1, "events", scratch_schema, "events", "loaded_at", 6, (
        ColumnTarget(1, "id", "integer", False), ColumnTarget(2, "email", "text", True),
        ColumnTarget(3, "channel", "text", False), ColumnTarget(4, "amount", "numeric", False),
        ColumnTarget(5, "not_in_table", "text", False),
    ))


def test_profile_statistics(db, settings, target):
    with db.connect() as conn:
        profile = Profiler(db, settings).profile_asset(conn, target)
    assert (profile.row_count, profile.column_count) == (4, 4)  # catalogued-but-missing column skipped
    cols = {c.column_name: c for c in profile.columns}
    assert (cols["id"].uniqueness, cols["id"].top_values) == (1.0, [])  # all distinct: no top values
    assert (cols["email"].null_count, cols["email"].null_rate) == (1, 0.25)
    assert cols["email"].min_value is None and cols["email"].sample_values == []  # PII never leaves the table
    assert cols["channel"].top_values == [{"value": "web", "count": 3}, {"value": "app", "count": 1}]
    assert (cols["amount"].mean_value, cols["amount"].median_value) == (20.0, 20.0)
    assert (cols["amount"].min_value, cols["amount"].max_value) == ("10.0", "30.0")
    assert profile.freshness_lag_hours == pytest.approx(3.0, abs=0.01)  # 21:00 vs 23:59:59
    assert profile.freshness_status == "fresh"


def test_missing_relation_raises(db, settings, target):
    gone = ProfileTarget(2, "gone", target.schema_name, "gone", None, None, target.columns)
    with db.connect() as conn, pytest.raises(DataTrustError, match="does not exist"):
        Profiler(db, settings).profile_asset(conn, gone)
