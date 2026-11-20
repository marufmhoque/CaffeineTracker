import copy

import pytest

from backend.app import costing
from backend.app.costing import (
    cost_breakdown,
    landed_cost,
    ltl_freight_per_lb,
    match_warehouse,
    roasted_cost,
    validate_rules,
)


@pytest.mark.parametrize(
    "location,tier,warehouse,southeast",
    [
        ("Savannah, GA", "southeast", "Savannah, GA", True),
        ("SAVANNAH warehouse #3", "southeast", "Savannah, GA", True),
        ("North Charleston SC", "southeast", "Charleston, SC", True),
        ("Jacksonville FL", "southeast", "Jacksonville, FL", True),
        ("Elizabeth, NJ", "east", "New Jersey", False),
        ("NJ", "east", "New Jersey", False),
        ("Brooklyn NY", "east", "New York, NY", False),
        ("Miami", "east", "Miami, FL", False),
        ("Oakland CA", "west", "Oakland, CA", False),
        ("Seattle", "west", "Seattle, WA", False),
        ("Houston, TX", "west", "Houston, TX", False),
    ],
)
def test_match_warehouse(rules, location, tier, warehouse, southeast):
    m = match_warehouse(location, rules)
    assert m.matched
    assert (m.tier_id, m.warehouse, m.southeast) == (tier, warehouse, southeast)


def test_default_freight_by_tier(rules):
    assert match_warehouse("Savannah", rules).freight_usd_lb == pytest.approx(0.15)
    assert match_warehouse("Newark NJ", rules).freight_usd_lb == pytest.approx(0.26)
    assert match_warehouse("Oakland", rules).freight_usd_lb == pytest.approx(0.45)


def test_unknown_warehouse_uses_conservative_default(rules):
    m = match_warehouse("Somewhere, Nowhere", rules)
    assert not m.matched
    assert m.tier_id == "unknown"
    assert m.freight_usd_lb == rules["unknown_warehouse_freight_usd_lb"]
    assert match_warehouse(None, rules).warehouse == "Unknown"


def test_short_aliases_ignored_in_prose(rules):
    text = "Sweet and juicy, many layers of fruit; a sea of florals"
    assert not match_warehouse(text, rules, allow_short=False).matched
    # but a real city in prose still matches
    assert match_warehouse("Stored in our Savannah facility", rules, allow_short=False).warehouse == "Savannah, GA"


def test_landed_and_roasted_formulas():
    assert landed_cost(4.10, 0.15, 0.05) == pytest.approx(4.30)
    # 15% shrink: 4.30 / 0.85
    assert roasted_cost(4.30, 0.15) == pytest.approx(5.0588235)
    assert roasted_cost(4.30, 0.0) == pytest.approx(4.30)
    with pytest.raises(ValueError):
        roasted_cost(4.30, 1.0)
    with pytest.raises(ValueError):
        landed_cost(-1, 0.1, 0.1)


def test_cost_breakdown_overrides(rules):
    bd, m = cost_breakdown(4.0, "Charleston", rules=rules)
    assert m.southeast
    assert bd.landed_usd_lb == pytest.approx(4.0 + 0.15 + 0.05)
    bd2, _ = cost_breakdown(4.0, "Charleston", rules=rules, freight_override=0.10, fee_override=0.0, shrinkage=0.18)
    assert bd2.landed_usd_lb == pytest.approx(4.10)
    assert bd2.roasted_usd_lb == pytest.approx(4.10 / 0.82)


def test_ltl_freight_per_lb():
    # $380 quote for 10 x 69 kg bags
    assert ltl_freight_per_lb(380, 10, 69) == pytest.approx(380 / (690 * 2.2046226), rel=1e-6)
    with pytest.raises(ValueError):
        ltl_freight_per_lb(380, 0, 69)


def test_validate_rules_rejects_bad_tiers(rules):
    validate_rules(rules)
    bad = copy.deepcopy(rules)
    bad["tiers"][0]["default"] = 9.0  # outside [low, high]
    with pytest.raises(ValueError):
        validate_rules(bad)
    bad = copy.deepcopy(rules)
    bad["default_shrinkage"] = 1.2
    with pytest.raises(ValueError):
        validate_rules(bad)


def test_rules_persist_roundtrip(tmp_path, rules):
    path = tmp_path / "rules.json"
    assert costing.load_rules(path)["warehouse_fee_usd_lb"] == rules["warehouse_fee_usd_lb"]  # written on first load
    rules["warehouse_fee_usd_lb"] = 0.08
    costing.save_rules(rules, path)
    assert costing.load_rules(path)["warehouse_fee_usd_lb"] == 0.08
