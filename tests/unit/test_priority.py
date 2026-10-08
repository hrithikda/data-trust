import copy

import pytest
import yaml

from datatrust.errors import ConfigurationError
from datatrust.priority.scoring import IssueSignals, PriorityModel
from tests.conftest import CONFIG_DIR
from tests.factories import analyzer

CONFIG = yaml.safe_load((CONFIG_DIR / "priority_model.yml").read_text())


@pytest.fixture(scope="module")
def model() -> PriorityModel:
    return PriorityModel(CONFIG)


def small_high_impact(model: PriorityModel):
    """3 duplicated order ids in a critical staging model feeding finance and the executive dashboard."""
    signals = IssueSignals("critical", records_failed=3, records_scanned=22_000, asset_criticality="critical",
                           critical_columns=[("order_id", "identifier")])
    return model.score(signals, analyzer().analyze("stg_orders", ["order_id"], row_level=True))


def large_low_impact(model: PriorityModel):
    """5,000 unexpected ticket channels in a low-criticality model nobody important reads."""
    signals = IssueSignals("low", records_failed=5_000, records_scanned=12_000, asset_criticality="low")
    return model.score(signals, analyzer().analyze("ticket_channel_mix", ["channel"]))


def test_small_high_impact_defect_outranks_large_unimportant_one(model):
    small, large = small_high_impact(model), large_low_impact(model)
    assert small.score > large.score + 40
    assert small.band in ("P1", "P2")
    assert large.band == "P4"
    # The large defect does win on volume; it loses on everything that matters downstream.
    volume = {r.key: r.points for r in large.factors}["affected_volume"]
    assert volume > {r.key: r.points for r in small.factors}["affected_volume"]
    assert small.blast_radius_points > large.blast_radius_points


def test_score_is_the_transparent_sum_of_capped_factors(model):
    result = small_high_impact(model)
    assert result.score == pytest.approx(min(100, sum(f.points for f in result.factors)), abs=0.11)
    assert result.score == pytest.approx(result.defect_points + result.blast_radius_points, abs=0.11)
    assert sum(f.max_points for f in result.factors) == 100
    for factor in result.factors:
        assert 0 <= factor.points <= factor.max_points
        assert factor.reason
    assert len(result.top_reasons(3)) == 3
    assert {row["key"] for row in result.breakdown()} == {f.key for f in result.factors}


def test_volume_is_log_scaled_and_capped(model):
    impact = analyzer().analyze("ticket_channel_mix")
    points = [
        {f.key: f.points for f in model.defect_factors(IssueSignals("low", n, 10**7, "low"))}["affected_volume"]
        for n in (1, 10, 100, 10_000, 10**6)
    ]
    assert points == sorted(points)
    assert points[-1] == points[-2] == CONFIG["factors"]["affected_volume"]["max_points"]
    assert model.impact_score(impact) == (0.0, "none")


def test_executive_points_decay_with_distance(model):
    near = {f.key: f.points for f in model.blast_radius_factors(analyzer().analyze("fin_revenue_close"))}
    far = {f.key: f.points for f in model.blast_radius_factors(analyzer().analyze("raw_orders"))}
    assert near["executive_or_customer_facing"] == 9  # executive output one hop away
    assert far["executive_or_customer_facing"] < near["executive_or_customer_facing"]
    assert far["executive_or_customer_facing"] >= CONFIG["factors"]["executive_or_customer_facing"][
        "executive_min_points"]


def test_bands(model):
    assert [model.band_for(s) for s in (100, 80, 79.9, 65, 45, 44.9, 0)] == ["P1", "P1", "P2", "P2", "P3", "P4", "P4"]


def test_invalid_configuration_is_rejected():
    unbalanced = copy.deepcopy(CONFIG)
    unbalanced["factors"]["severity"]["max_points"] = 30
    with pytest.raises(ConfigurationError, match="sum to 100"):
        PriorityModel(unbalanced)
    missing = copy.deepcopy(CONFIG)
    del missing["factors"]["multi_team"]
    with pytest.raises(ConfigurationError, match="missing factors"):
        PriorityModel(missing)
